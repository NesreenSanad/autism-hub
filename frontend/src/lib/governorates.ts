export const GOVERNORATES: [code: string, ar: string, en: string][] = [
  ["cairo", "القاهرة", "Cairo"], ["giza", "الجيزة", "Giza"], ["alexandria", "الإسكندرية", "Alexandria"],
  ["qalyubia", "القليوبية", "Qalyubia"], ["sharqia", "الشرقية", "Sharqia"], ["dakahlia", "الدقهلية", "Dakahlia"],
  ["gharbia", "الغربية", "Gharbia"], ["monufia", "المنوفية", "Monufia"], ["beheira", "البحيرة", "Beheira"],
  ["kafr_el_sheikh", "كفر الشيخ", "Kafr El Sheikh"], ["damietta", "دمياط", "Damietta"],
  ["port_said", "بورسعيد", "Port Said"], ["ismailia", "الإسماعيلية", "Ismailia"], ["suez", "السويس", "Suez"],
  ["north_sinai", "شمال سيناء", "North Sinai"], ["south_sinai", "جنوب سيناء", "South Sinai"],
  ["faiyum", "الفيوم", "Faiyum"], ["beni_suef", "بني سويف", "Beni Suef"], ["minya", "المنيا", "Minya"],
  ["asyut", "أسيوط", "Asyut"], ["sohag", "سوهاج", "Sohag"], ["qena", "قنا", "Qena"], ["luxor", "الأقصر", "Luxor"],
  ["aswan", "أسوان", "Aswan"], ["red_sea", "البحر الأحمر", "Red Sea"], ["new_valley", "الوادي الجديد", "New Valley"],
  ["matrouh", "مطروح", "Matrouh"],
];

export const governorateName = (code?: string) => {
  const hit = GOVERNORATES.find(([c]) => c === code);
  return hit ? `${hit[2]} · ${hit[1]}` : code ?? "—";
};
