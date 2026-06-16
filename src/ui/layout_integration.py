"""Layout Designer entry point + minimal generate flow (ROADMAP M3).

Adds an "AI Edit" action to every Print Layout Designer window. Triggering it:
captures the layout's map frame at print resolution (M2), asks for a prompt,
runs it through the existing generation pipeline (the same `GenerationTask`,
client, auth, and GeoTIFF writer the canvas flow uses), and adds the
georeferenced result as a layer in the project.

This is deliberately minimal — prompt in, layer out — so the Print Layout path
is usable end to end against the mock backend. Richer UI (reference images,
markup, templates) can be layered back in later.

Imports QGIS, so it is verified inside QGIS (see docs/ROADMAP.md M3), not in the
headless test container.
"""
from __future__ import annotations

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction,
    QInputDialog,
    QMessageBox,
    QProgressDialog,
)

from ..core.generation.generation_service import GenerationService
from ..core.generation.pipeline_context import PipelineContext
from ..core.i18n import tr
from ..core.logger import log_debug, log_warning
from ..workers.generation_worker import GenerationTask
from .composer_capture import capture_layout_map
from .raster_writer import add_geotiff_to_project, get_output_dir

_ACTION_OBJECT_NAME = "cartomancyLayoutGenerateAction"
_TOOLBAR_OBJECT_NAME = "cartomancyLayoutToolbar"


class LayoutGenerationController:
    """Owns the Layout Designer integration for the plugin's lifetime.

    Reuses the plugin's shared `client` and `auth_manager`; uses its own
    `GenerationService` so a layout generation can't clash with a canvas one.
    """

    def __init__(
        self,
        iface,
        client,
        auth_manager,
        plugin_dir,
        skip_trial_check: bool = False,
        dev_mode: bool = False,
    ):
        self._iface = iface
        self._client = client
        self._auth_manager = auth_manager
        self._plugin_dir = plugin_dir
        self._skip_trial_check = skip_trial_check
        self._dev_mode = dev_mode

        self._service = GenerationService(client)
        self._entries: list[dict] = []  # one per designer: action/toolbar/menu refs
        self._worker: GenerationTask | None = None
        self._progress: QProgressDialog | None = None
        self._icon_path = os.path.join(plugin_dir, "resources", "icons", "icon.png")

    # -- lifecycle --------------------------------------------------------- #
    def install(self) -> None:
        """Hook future designers, and decorate any already-open ones."""
        try:
            self._iface.layoutDesignerOpened.connect(self._on_designer_opened)
        except Exception as err:  # noqa: BLE001
            log_warning(f"Could not connect layoutDesignerOpened: {err}")
        try:
            for designer in self._iface.openLayoutDesigners():
                self._on_designer_opened(designer)
        except Exception as err:  # noqa: BLE001
            log_warning(f"Could not enumerate open layout designers: {err}")

    def uninstall(self) -> None:
        try:
            self._iface.layoutDesignerOpened.disconnect(self._on_designer_opened)
        except (RuntimeError, TypeError):
            pass
        self._cancel_active()
        for entry in self._entries:
            self._teardown_entry(entry)
        self._entries.clear()

    # -- designer decoration ---------------------------------------------- #
    def _on_designer_opened(self, designer) -> None:
        if any(e["designer"] is designer for e in self._entries):
            return
        try:
            window = designer.view().window()
        except Exception as err:  # noqa: BLE001
            log_warning(f"Could not resolve layout designer window: {err}")
            return
        if window is None:
            return

        icon = QIcon(self._icon_path) if os.path.exists(self._icon_path) else QIcon()
        action = QAction(icon, tr("Generate from layout…"), window)
        action.setObjectName(_ACTION_OBJECT_NAME)
        action.setToolTip(
            tr("AI Edit: generate from this layout at its print resolution")
        )
        action.triggered.connect(lambda _checked=False, d=designer: self._on_generate(d))

        toolbar = None
        menu = None
        try:
            toolbar = window.addToolBar(tr("AI Edit"))
            toolbar.setObjectName(_TOOLBAR_OBJECT_NAME)
            toolbar.addAction(action)
        except Exception as err:  # noqa: BLE001
            log_warning(f"Could not add layout toolbar: {err}")
        try:
            menu = window.menuBar().addMenu(tr("AI Edit"))
            menu.addAction(action)
        except Exception as err:  # noqa: BLE001
            log_warning(f"Could not add layout menu: {err}")

        self._entries.append(
            {"designer": designer, "window": window,
             "action": action, "toolbar": toolbar, "menu": menu}
        )
        log_debug("AI Edit action added to a layout designer")

    @staticmethod
    def _teardown_entry(entry: dict) -> None:
        window, action = entry.get("window"), entry.get("action")
        toolbar, menu = entry.get("toolbar"), entry.get("menu")
        try:
            if toolbar is not None:
                if window is not None:
                    window.removeToolBar(toolbar)
                toolbar.deleteLater()
            if menu is not None:
                menu.deleteLater()
            if action is not None:
                action.deleteLater()
        except RuntimeError:
            # The designer window was already destroyed (user closed it); Qt
            # tore down our child widgets with it. Nothing to clean up.
            pass

    # -- the generate flow ------------------------------------------------- #
    def _on_generate(self, designer) -> None:
        window = None
        for entry in self._entries:
            if entry["designer"] is designer:
                window = entry["window"]
                break

        if self._worker is not None and self._worker.is_active():
            self._warn(window, tr("A generation is already running. Please wait."))
            return

        layout = designer.layout()
        if layout is None:
            self._warn(window, tr("No layout is open."))
            return

        # 1) Capture the map frame at print resolution (M2).
        ctx = PipelineContext()
        try:
            capture = capture_layout_map(layout, ctx=ctx)
        except ValueError as err:
            # User-fixable: no reference map, rotated map, bad paper size/DPI.
            self._warn(window, str(err))
            return
        except RuntimeError as err:
            # Export config not loaded yet (needs the backend reachable).
            self._warn(window, str(err))
            return
        except Exception as err:  # noqa: BLE001
            log_warning(f"Layout capture failed: {err}")
            self._warn(window, tr("Could not read the layout map: {err}").format(err=err))
            return

        # 2) Prompt.
        prompt, ok = QInputDialog.getMultiLineText(
            window,
            tr("AI Edit — generate from layout"),
            tr(
                "Describe the edit. Output: {w}x{h}px ({tier}), "
                "georeferenced to the map frame."
            ).format(w=capture.width_px, h=capture.height_px, tier=capture.resolution_tier),
            "",
        )
        if not ok or not prompt.strip():
            return
        prompt = prompt.strip()

        # 3) Credit pre-flight (skippable in dev via SKIP_TRIAL_CHECK).
        if not self._skip_trial_check:
            try:
                allowed, reason, _code = self._auth_manager.check_can_generate()
            except Exception as err:  # noqa: BLE001
                log_warning(f"Pre-generation check raised: {err}")
                allowed, reason = False, tr(
                    "No internet connection. Check your network and try again."
                )
            if not allowed:
                self._warn(window, reason)
                return

        # 4) Launch the existing generation pipeline.
        self._service.reset()
        self._show_progress(window)
        self._worker = GenerationTask(
            client=self._client,
            auth_manager=self._auth_manager,
            service=self._service,
            image_b64=capture.image_b64,
            prompt=prompt,
            aspect_ratio="auto",
            extent_dict=capture.extent_dict,
            crs_wkt=capture.crs_wkt,
            output_dir=get_output_dir(),
            suggested_resolution=capture.resolution_tier,
            ctx=ctx,
            debug_mode=self._dev_mode,
            plugin_dir=self._plugin_dir,
            skip_trial_check=self._skip_trial_check,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        QgsApplication.taskManager().addTask(self._worker)
        log_debug(
            f"Layout generation submitted: tier={capture.resolution_tier}, "
            f"size={capture.width_px}x{capture.height_px}"
        )

    # -- worker signal handlers ------------------------------------------- #
    def _on_progress(self, status: str, percentage: int) -> None:
        if self._progress is not None:
            self._progress.setLabelText(status)
            self._progress.setValue(max(0, min(100, int(percentage))))

    def _on_succeeded(self, result_info: dict) -> None:
        self._close_progress()
        self._worker = None
        try:
            layer = add_geotiff_to_project(
                result_info["geotiff_path"],
                result_info.get("prompt", ""),
                crs_wkt=result_info.get("crs_wkt", ""),
            )
            try:
                self._iface.setActiveLayer(layer)
            except Exception as err:  # noqa: BLE001
                log_warning(f"setActiveLayer failed: {err}")
            self._info(
                tr("AI Edit"),
                tr("Added '{name}' to the project.").format(name=layer.name()),
            )
        except Exception as err:  # noqa: BLE001
            log_warning(f"Adding generated layer failed: {err}")
            self._warn(None, tr("The result could not be added: {err}").format(err=err))

    def _on_failed(self, message: str, code: str, _ctx_snapshot: dict | None = None) -> None:
        self._close_progress()
        self._worker = None
        self._warn(None, message or tr("Generation failed."))

    # -- progress dialog --------------------------------------------------- #
    def _show_progress(self, parent) -> None:
        self._progress = QProgressDialog(
            tr("Generating…"), tr("Cancel"), 0, 100, parent
        )
        self._progress.setWindowTitle(tr("AI Edit"))
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)
        self._progress.setValue(1)
        self._progress.canceled.connect(self._cancel_active)
        self._progress.show()

    def _close_progress(self) -> None:
        if self._progress is not None:
            try:
                self._progress.reset()
                self._progress.deleteLater()
            except RuntimeError:
                pass
            self._progress = None

    def _cancel_active(self) -> None:
        if self._worker is not None:
            try:
                self._service.cancel()
            except Exception as err:  # noqa: BLE001
                log_warning(f"Service cancel failed: {err}")
            try:
                self._worker.cancel()
            except Exception:  # noqa: BLE001  # nosec B110
                pass
            self._worker = None
        self._close_progress()

    # -- small message helpers -------------------------------------------- #
    def _warn(self, parent, text: str) -> None:
        QMessageBox.warning(parent, tr("AI Edit"), text)

    def _info(self, title: str, text: str) -> None:
        try:
            self._iface.messageBar().pushInfo(title, text)
        except Exception:  # noqa: BLE001
            QMessageBox.information(None, title, text)
