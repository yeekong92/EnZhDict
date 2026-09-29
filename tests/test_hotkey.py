from dictapp.platform_support.hotkey import DoubleTapDetector


def tap(d, t, hold=0.05):
    d.press(True, t)
    return d.release(True, t + hold)


def test_double_tap_fires():
    d = DoubleTapDetector(400)
    assert not tap(d, 0.0)
    assert tap(d, 0.25)


def test_too_slow():
    d = DoubleTapDetector(400)
    tap(d, 0.0)
    assert not tap(d, 0.6)
    assert tap(d, 0.9)  # the slow second tap starts a new sequence


def test_interval_is_configurable():
    d = DoubleTapDetector(700)
    tap(d, 0.0)
    assert tap(d, 0.6)


def test_ctrl_combo_breaks_sequence():
    d = DoubleTapDetector(400)
    tap(d, 0.0)
    d.press(True, 0.1)       # Ctrl+C
    d.press(False, 0.12)
    d.release(False, 0.15)
    assert not d.release(True, 0.18)
    assert not tap(d, 0.3)   # previous tap was forgotten


def test_typing_between_taps_breaks_sequence():
    d = DoubleTapDetector(400)
    tap(d, 0.0)
    d.press(False, 0.1)
    d.release(False, 0.12)
    assert not tap(d, 0.2)


def test_long_hold_is_not_a_tap():
    d = DoubleTapDetector(400)
    tap(d, 0.0, hold=0.5)
    assert not tap(d, 0.6)


def test_autorepeat_ignored():
    d = DoubleTapDetector(400)
    tap(d, 0.0)
    d.press(True, 0.2)
    d.press(True, 0.23)      # key auto-repeat while held
    assert d.release(True, 0.28)


def test_triple_tap_fires_once():
    d = DoubleTapDetector(400)
    assert [tap(d, 0.0), tap(d, 0.2), tap(d, 0.4)] == [False, True, False]
