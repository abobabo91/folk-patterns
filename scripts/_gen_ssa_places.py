"""Generate a places dict for Sub-Saharan Africa via Claude CLI."""
import subprocess, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

prompt = """Generate a Python dict for routing museum "place" strings to countries in Sub-Saharan Africa.

Target countries with matching ethnicities from this cohort:
- Senegal: Wolof, Fulani
- Nigeria: Yoruba, Igbo
- Ghana: Ashanti
- Ethiopia: Amhara, Oromo, Tigray, Sidama
- Kenya: Kikuyu, Maasai
- Tanzania: Swahili
- Democratic Republic of the Congo (DRC): Kongo, Kuba, Chokwe
- Gabon: Fang
- Central African Republic: Mbuti
- South Africa: Zulu, Xhosa, Ndebele, Sotho
- Namibia: Himba
- Botswana: San
- Somalia: Somali
- Djibouti / Eritrea: Afar

Additionally include colonial-era exonyms and French/German/Dutch spellings that appear in Musée du quai Branly (French), Royal Museum for Central Africa Tervuren (French/Dutch), Ethnologisches Museum Berlin (German), British Museum records. E.g. "Congo belge", "Kongostaat", "Basutoland" (=Lesotho, near Sotho), "Nyasaland" (Malawi), "German East Africa" (Tanzania), "Belgisch Kongo", "Afrique-Occidentale française", "Deutsch-Ostafrika", etc.

Return ONLY a JSON object with these three keys:
{
  "place_to_country": {
    // lowercase place strings → exact country name from the list above OR "_regional" for pan-region
  },
  "reject_places": [
    // Places clearly OUTSIDE Sub-Saharan Africa (Central Asian, SE Asian, MENA, Americas, Europe, etc.)
  ],
  "signature_traditions": {
    // Traditions unambiguous to one country, e.g. "kente" -> "Ghana", "kuba cloth" -> "Democratic Republic of the Congo", "adinkra" -> "Ghana", "byeri" -> "Gabon"
  }
}

Aim for 80-120 place entries and 20-30 signature traditions. No prose, no fences."""

proc = subprocess.run('claude --print --model claude-opus-5', shell=True,
                      input=prompt.encode('utf-8'), capture_output=True, timeout=300)
sys.stdout.write(proc.stdout.decode('utf-8', errors='replace'))
