from collector import normalize

GOVERNORATES = normalize.GovernorateMatcher([
    ("cairo", "القاهرة", "Cairo"), ("alexandria", "الإسكندرية", "Alexandria"),
    ("kafr_el_sheikh", "كفر الشيخ", "Kafr El Sheikh"),
])


def test_clean_text_fixes_presentation_forms():
    assert normalize.clean_text("  ﻧﻬﺎل   محمد ") == "نهال محمد"
    assert normalize.clean_text("   ") is None


def test_search_name_unifies_letters_and_drops_titles():
    assert normalize.search_name("د. أحمد إبراهيم") == "احمد ابراهيم"
    assert normalize.search_name("دكتورة مُنى") == "مني"
    assert normalize.search_name("أ.د/ هالة") == "هاله"
    assert normalize.search_name("Dr. Mona Ali") == "mona ali"


def test_phone():
    assert normalize.phone("010 0123 4567") == "+201001234567"
    assert normalize.phone("+20 10 01234567") == "+201001234567"
    assert normalize.phone("00201001234567") == "+201001234567"
    assert normalize.phone("٠١٠٠١٢٣٤٥٦٧") == "+201001234567"
    assert normalize.phone("02 2345 6789") == "+20223456789"
    assert normalize.phone("03 4567890") == "+2034567890"
    assert normalize.phone("19123") == "19123"
    assert normalize.phone("call us") is None


def test_governorates():
    assert GOVERNORATES.match("محافظة القاهرة") == "cairo"
    assert GOVERNORATES.match("Cairo Governorate") == "cairo"
    assert GOVERNORATES.match("الاسكندرية") == "alexandria"
    assert GOVERNORATES.match("kafr_el_sheikh") == "kafr_el_sheikh"
    assert GOVERNORATES.find_in("شارع النصر، مدينة نصر، محافظة القاهرة، مصر") == "cairo"
    assert GOVERNORATES.match("Paris") is None


def test_code_list_keeps_known_codes():
    assert normalize.code_list("speech; OT, unknown", ["speech", "ot"]) == ["ot", "speech"]


def test_hash_ignores_key_order_and_empty_values():
    a = normalize.build_record({"name_ar": "مركز", "phone": "+201001234567", "website": ""})
    b = normalize.build_record({"phone": "+201001234567", "name_ar": " مركز "})
    assert normalize.content_hash(a) == normalize.content_hash(b)
