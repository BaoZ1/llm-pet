import base64
from dataclasses import dataclass
from io import BytesIO
import sys
from typing import cast
import webbrowser
from framework.plugin import BasePlugin, Tool
from framework.config import BaseConfig
import pathlib
import pywinauto
from pywinauto import WindowSpecification
from pywinauto.base_wrapper import BaseWrapper
from pywinauto.win32structures import RECT
from PIL import ImageGrab
from langchain_core.messages import HumanMessage


def control_structure(window: WindowSpecification | BaseWrapper, depth=None):
    if depth is None:
        depth = sys.maxsize

    if isinstance(window, WindowSpecification):
        window = window.wrapper_object()

    all_ctrls = [
        window,
    ] + window.descendants()

    txt_ctrls = [
        ctrl
        for ctrl in all_ctrls
        if ctrl.can_be_label and ctrl.is_visible() and ctrl.window_text()
    ]

    name_ctrl_id_map = pywinauto.findbestmatch.UniqueDict()
    for index, ctrl in enumerate(all_ctrls):
        ctrl_names = pywinauto.findbestmatch.get_control_names(
            ctrl, all_ctrls, txt_ctrls
        )
        for name in ctrl_names:
            name_ctrl_id_map[name] = index

    ctrl_id_name_map = {}
    for name, index in name_ctrl_id_map.items():
        ctrl_id_name_map.setdefault(index, []).append(name)

    def get_structure(ctrl: BaseWrapper, current_depth: int):
        d = {}

        d["class_name"] = ctrl.friendly_class_name()

        ctrl_text: str = ctrl.window_text()
        ctrl_text = ctrl_text.replace("\n", r"\n").replace("\r", r"\r")
        d["text"] = ctrl_text

        rect: RECT = ctrl.rectangle()
        d["rect"] = {
            "left": rect.left,
            "right": rect.right,
            "top": rect.top,
            "bottom": rect.bottom,
        }

        if hasattr(ctrl.element_info, "automation_id"):
            d["auto_id"] = ctrl.element_info.automation_id
        if hasattr(ctrl.element_info, "control_type"):
            d["control_type"] = ctrl.element_info.control_type

        if current_depth == depth:
            d["children"] = "depth reached"
        else:
            d["children"] = [
                get_structure(child, current_depth + 1) for child in ctrl.children()
            ]

        return d

    return get_structure(window, 0)


class ReadDesktop(Tool):
    def invoke(self):
        """Get structured data of items on the user's desktop"""

        return {
            "path": pathlib.Path("~/Desktop").expanduser(),
            "items": control_structure(
                cast(Plugin, self.plugin).desktop,
                1,
            )["children"],
        }


class OpenBrowser(Tool):
    def invoke(self, url: str):
        """Open a specified webpage or local file in the browser

        Args:
            url: the url of the website or the local file(file://...)
        """
        webbrowser.open(url)
        return "success"


class ScreenShot(Tool):
    response_format = "content_and_artifact"

    def invoke(self):
        """Get a screenshot of user's device"""
        screenshot = ImageGrab.grab().convert("RGB")

        buffer = BytesIO()

        screenshot.save(buffer, format="JPEG", quality=85)

        img_bytes = buffer.getvalue()
        base64_string = base64.b64encode(img_bytes).decode("utf-8")

        return "success", HumanMessage(
            [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_string}",
                    },
                }
            ]
        )


@dataclass
class Config(BaseConfig):
    desktop_access: bool = False
    enable_screenshot: bool = False


class Plugin(BasePlugin):
    def init(self):
        self._desktop: BaseWrapper | None = None

    @property
    def desktop(self):
        if self._desktop is None:
            self._desktop = (
                pywinauto.Desktop("uia").window(auto_id="1").wrapper_object()
            )
        return self._desktop

    def tools(self):
        l = [OpenBrowser]
        config = cast(Config, self.get_config())
        if config.desktop_access:
            l.append(ReadDesktop)
        if config.enable_screenshot:
            l.append(ScreenShot)
        return l
