from surveybot.Survey.batch_response_parser import _is_exclusive_value


def test_exclusive_dont_know_variants_are_detected_with_prefix_match():
    assert _is_exclusive_value("Ne sait pas")
    assert _is_exclusive_value("Je ne sais pas")
    assert _is_exclusive_value("NSP")
    assert _is_exclusive_value("Don't know")
    assert _is_exclusive_value("DK")
    assert _is_exclusive_value("Not sure")
    assert _is_exclusive_value("Unsure")


def test_exclusive_dont_know_variants_do_not_match_mid_sentence():
    assert not _is_exclusive_value("Réponse: je ne sais pas encore si cela s'applique")
    assert not _is_exclusive_value("Option where people don't know yet")
    assert not _is_exclusive_value("Some users are unsure about this")
