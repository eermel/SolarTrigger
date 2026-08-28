from plugins.camera import get_camera_model


class FakeAbility:
    model = "USB PTP Class Camera"


class FakeConfigWidget:
    def get_value(self):
        return "Sony ILCE-7M5 (PC Control)"


class FakeConfig:
    def get_child_by_name(self, name):
        if name == "cameramodel":
            return FakeConfigWidget()
        raise RuntimeError(name)


class FakeCamera:
    def get_abilities(self):
        return FakeAbility()

    def get_config(self):
        return FakeConfig()


def test_specific_config_model_wins_over_generic_ptp_ability():
    assert get_camera_model(FakeCamera()) == "Sony ILCE-7M5 (PC Control)"
