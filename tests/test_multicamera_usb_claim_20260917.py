from services.camera_service import CameraService
import services.camera_service as camera_service


class FakePortInfoList:
    def load(self):
        return None

    def lookup_path(self, path):
        return {
            "usb:001,002": 0,
            "usb:001,003": 1,
        }[path]

    def __getitem__(self, index):
        return ["SONY_PORT", "NIKON_PORT"][index]


class FakeCamera:
    detected = [
        ("Sony ILCE-7M5", "usb:001,002"),
        ("Nikon DSC D850", "usb:001,003"),
    ]

    initialized_ports = []

    @classmethod
    def autodetect(cls):
        return list(cls.detected)

    def __init__(self):
        self.port = None

    def set_port_info(self, port):
        self.port = port

    def init(self):
        self.initialized_ports.append(self.port)

    def exit(self):
        return None


class FakeGp:
    Camera = FakeCamera
    PortInfoList = FakePortInfoList


def test_multicamera_usb_serial_does_not_probe_other_body(monkeypatch):
    FakeCamera.initialized_ports.clear()

    identities = {
        "usb:001,002": {"serial": "SONY-D19"},
        "usb:001,003": {"serial": "NIKON-429"},
    }

    monkeypatch.setattr(
        camera_service,
        "_usb_identity",
        lambda port: identities[port],
    )

    service = CameraService()

    camera = service._open_camera_by_serial(
        FakeGp,
        "NIKON-429",
    )

    assert camera.port == "NIKON_PORT"

    # Critical regression assertion:
    # Sony must never have been camera.init()'d while locating Nikon.
    assert FakeCamera.initialized_ports == ["NIKON_PORT"]


def test_single_camera_keeps_legacy_protocol_serial_fallback(monkeypatch):
    FakeCamera.initialized_ports.clear()
    FakeCamera.detected = [
        ("Legacy Camera", "usb:001,002"),
    ]

    monkeypatch.setattr(
        camera_service,
        "_usb_identity",
        lambda _port: {"serial": "USB-OTHER"},
    )

    class Value:
        def get_value(self):
            return "LEGACY-PTP-SERIAL"

    class Config:
        def get_child_by_name(self, _name):
            return Value()

    original_get_config = getattr(FakeCamera, "get_config", None)
    FakeCamera.get_config = lambda self: Config()

    try:
        service = CameraService()

        camera = service._open_camera_by_serial(
            FakeGp,
            "LEGACY-PTP-SERIAL",
        )

        assert camera.port == "SONY_PORT"
        assert FakeCamera.initialized_ports == ["SONY_PORT"]
    finally:
        FakeCamera.detected = [
            ("Sony ILCE-7M5", "usb:001,002"),
            ("Nikon DSC D850", "usb:001,003"),
        ]

        if original_get_config is None:
            delattr(FakeCamera, "get_config")
        else:
            FakeCamera.get_config = original_get_config
