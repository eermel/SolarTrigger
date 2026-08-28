import math
from decimal import Decimal, localcontext

import pytest

from backend.field_rotation import (
    FieldRotationSingularityError,
    field_rotation_rate_deg_s,
)


SIDEREAL_DAY_SEC = 86164.0905
PI_DECIMAL = Decimal("3.1415926535897932384626433832795028841971693993751")


def to_rad(angle_deg):
    return math.radians(angle_deg)


def to_deg(angle_rad):
    return math.degrees(angle_rad)


def normalize_hour_angle(angle_deg):
    return (angle_deg + 180.0) % 360.0 - 180.0


def parallactic_components(phi, delta, hour_angle):
    y = math.sin(hour_angle)
    x = math.tan(phi) * math.cos(delta) - math.sin(delta) * math.cos(hour_angle)
    return x, y


def q_meeus(phi, delta, hour_angle):
    y = math.sin(hour_angle)
    x = math.tan(phi) * math.cos(delta) - math.sin(delta) * math.cos(hour_angle)
    return math.atan2(y, x)


def sin_cos_decimal(angle):
    sine_term = angle
    cosine_term = Decimal(1)
    sine = sine_term
    cosine = cosine_term
    index = 1
    while True:
        sine_term *= -(angle * angle) / Decimal((2 * index) * (2 * index + 1))
        cosine_term *= -(angle * angle) / Decimal(
            (2 * index - 1) * (2 * index)
        )
        next_sine = sine + sine_term
        next_cosine = cosine + cosine_term
        if next_sine == sine and next_cosine == cosine:
            return sine, cosine
        sine, cosine = next_sine, next_cosine
        index += 1


def parallactic_components_decimal(phi, delta, hour_angle):
    sin_phi, cos_phi = sin_cos_decimal(phi)
    sin_delta, cos_delta = sin_cos_decimal(delta)
    sin_hour_angle, cos_hour_angle = sin_cos_decimal(hour_angle)
    x = (sin_phi / cos_phi) * cos_delta - sin_delta * cos_hour_angle
    return x, sin_hour_angle


def finite_difference_rate_deg_s(latitude_deg, declination_deg, hour_angle_deg):
    dt = Decimal("1e-4")
    with localcontext() as context:
        context.prec = 50
        omega = Decimal(2) * PI_DECIMAL / Decimal(str(SIDEREAL_DAY_SEC))
        phi = Decimal(str(latitude_deg)) * PI_DECIMAL / Decimal(180)
        delta = Decimal(str(declination_deg)) * PI_DECIMAL / Decimal(180)
        hour_angle = (
            Decimal(str(normalize_hour_angle(hour_angle_deg)))
            * PI_DECIMAL
            / Decimal(180)
        )
        x_before, y_before = parallactic_components_decimal(
            phi, delta, hour_angle - omega * dt
        )
        x_after, y_after = parallactic_components_decimal(
            phi, delta, hour_angle + omega * dt
        )
        q_delta = math.atan2(
            float(y_after * x_before - x_after * y_before),
            float(x_after * x_before + y_after * y_before),
        )
    return to_deg(q_delta / (2.0 * float(dt)))


@pytest.mark.parametrize(
    ("latitude_deg", "declination_deg", "hour_angle_deg", "expected_deg_s"),
    [
        (40.0, 10.0, 30.0, 0.00320325931226223),
        (51.5, -20.0, -45.0, 0.00194778030682210),
        (24.0, 24.0, 0.01, -0.000849688028008882),
    ],
)
def test_fixed_reference_vectors_and_independent_finite_difference(
    latitude_deg, declination_deg, hour_angle_deg, expected_deg_s
):
    result = field_rotation_rate_deg_s(
        latitude_deg, declination_deg, hour_angle_deg
    )

    assert result == pytest.approx(expected_deg_s, abs=1e-12)
    assert result == pytest.approx(
        finite_difference_rate_deg_s(
            latitude_deg, declination_deg, hour_angle_deg
        ),
        abs=1e-9,
    )


def test_undefined_parallactic_angle_raises_singularity_error():
    with pytest.raises(FieldRotationSingularityError):
        field_rotation_rate_deg_s(24.0, 24.0, 0.0)


def test_equator_zero_declination_has_zero_rotation():
    result = field_rotation_rate_deg_s(0.0, 0.0, 45.0)

    assert result == 0.0 or abs(result) < 1e-15


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("argument_index", [0, 1, 2])
def test_non_finite_inputs_are_rejected(invalid_value, argument_index):
    arguments = [40.0, 10.0, 30.0]
    arguments[argument_index] = invalid_value

    with pytest.raises(ValueError):
        field_rotation_rate_deg_s(*arguments)


@pytest.mark.parametrize("argument_index", [0, 1, 2])
def test_bool_inputs_are_rejected(argument_index):
    arguments = [40.0, 10.0, 30.0]
    arguments[argument_index] = True

    with pytest.raises(TypeError):
        field_rotation_rate_deg_s(*arguments)


@pytest.mark.parametrize("latitude_deg", [-90.0, 90.0, -90.1, 90.1])
def test_latitude_outside_open_domain_is_rejected(latitude_deg):
    with pytest.raises(ValueError):
        field_rotation_rate_deg_s(latitude_deg, 10.0, 30.0)


@pytest.mark.parametrize("declination_deg", [-90.1, 90.1])
def test_declination_outside_closed_domain_is_rejected(declination_deg):
    with pytest.raises(ValueError):
        field_rotation_rate_deg_s(40.0, declination_deg, 30.0)


@pytest.mark.parametrize("equivalent_hour_angle", [390.0, -330.0])
def test_hour_angle_is_normalized_to_half_open_domain(equivalent_hour_angle):
    expected = field_rotation_rate_deg_s(40.0, 10.0, 30.0)

    assert field_rotation_rate_deg_s(
        40.0, 10.0, equivalent_hour_angle
    ) == pytest.approx(expected, abs=1e-15)


def test_positive_180_hour_angle_normalizes_to_negative_180():
    assert field_rotation_rate_deg_s(
        40.0, 10.0, 180.0
    ) == field_rotation_rate_deg_s(40.0, 10.0, -180.0)
