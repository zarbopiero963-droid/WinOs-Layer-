"""AT-SPI control identity must survive changing presentation names."""
from __future__ import annotations

from windows_os_api.backends.linux import LinuxBackend


class _Accessible:
    def __init__(self, name: str, role: str, children=None, application_id=None):
        self.name = name
        self._role = role
        self._children = list(children or [])
        self.id = application_id

    @property
    def childCount(self):
        return len(self._children)

    def getChildAtIndex(self, index):
        return self._children[index]

    def getRoleName(self):
        return self._role

    def getApplication(self):
        return self


def test_structural_id_finds_a_text_node_after_its_name_changes():
    text = _Accessible("old contents", "text")
    frame = _Accessible("Untitled 1 - Mousepad", "frame", [text])
    app = _Accessible("mousepad", "application", [frame], application_id=42)
    desktop = _Accessible("desktop", "desktop frame", [app])

    class _Registry:
        @staticmethod
        def getDesktop(_index):
            return desktop

    class _PyAtSpi:
        Registry = _Registry

    backend = object.__new__(LinuxBackend)
    tree = backend._atspi_node(app, _PyAtSpi, sibling_index=0)
    text_id = tree["children"][0]["children"][0]["automation_id"]

    text.name = "new contents"
    desktop._children.insert(
        0, _Accessible("another-app", "application", application_id=7)
    )

    assert "mousepad" in text_id.casefold()
    assert "old contents" not in text_id
    assert backend._find_atspi_acc_by_path(_PyAtSpi, text_id) is text
