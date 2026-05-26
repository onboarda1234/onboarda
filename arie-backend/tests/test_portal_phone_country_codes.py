from html.parser import HTMLParser
from pathlib import Path


PORTAL_PATH = Path(__file__).resolve().parents[2] / "arie-portal.html"


class PhoneSelectParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.selects = {}
        self._select_id = None
        self._current_option = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "select":
            select_id = attrs_dict.get("id")
            self._select_id = select_id if select_id in {"r-phone-code", "f-phone-code"} else None
            if self._select_id:
                self.selects.setdefault(self._select_id, [])
        elif tag == "option" and self._select_id:
            self._current_option = {"attrs": attrs_dict, "text": ""}

    def handle_data(self, data):
        if self._current_option is not None:
            self._current_option["text"] += data

    def handle_endtag(self, tag):
        if tag == "option" and self._select_id and self._current_option is not None:
            self.selects[self._select_id].append(self._current_option)
            self._current_option = None
        elif tag == "select":
            self._select_id = None


def _read_portal():
    return PORTAL_PATH.read_text(encoding="utf-8")


def _phone_selects():
    parser = PhoneSelectParser()
    parser.feed(_read_portal())
    return parser.selects


def _option_by_value(options, value):
    matches = [opt for opt in options if opt["attrs"].get("value") == value]
    assert matches, f"Missing phone option {value}"
    return matches[0]


def test_all_portal_phone_dropdowns_include_mauritius_option():
    selects = _phone_selects()

    assert set(selects) == {"r-phone-code", "f-phone-code"}
    for select_id in ("r-phone-code", "f-phone-code"):
        mauritius = _option_by_value(selects[select_id], "+230")
        assert mauritius["attrs"].get("data-country-code") == "MU"
        assert mauritius["attrs"].get("data-country-name") == "Mauritius"
        assert "Mauritius" in mauritius["text"]


def test_prescreening_phone_dropdown_defaults_to_mauritius_not_uae():
    options = _phone_selects()["f-phone-code"]
    selected_values = [
        opt["attrs"].get("value")
        for opt in options
        if "selected" in opt["attrs"]
    ]

    assert selected_values == ["+230"]
    assert "selected" not in _option_by_value(options, "+971")["attrs"]


def test_prescreening_phone_code_storage_contract_is_unchanged():
    html = _read_portal()

    assert "'f-phone-code': 'entity_contact_phone_code'" in html
    assert "'f-mobile': 'entity_contact_mobile'" in html
    assert "entity_contact_phone_code: getFieldValue('f-phone-code')" in html
    assert "entity_contact_mobile: getFieldValue('f-mobile')" in html
    assert "form.querySelectorAll('input, select, textarea')" in html
