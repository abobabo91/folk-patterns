"""Ask Claude for the MENA places dict."""
import subprocess, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

prompt = """Generate a MENA_PLACES dictionary matching this exact Python structure — for routing museum "place" strings to countries in the Middle East / North Africa region. Countries: Iran, Morocco, Tunisia, Egypt, Turkey.

Rules:
- Keys: lowercase city/region/country strings as museums actually catalog them (include colonial-language variants: French for Morocco/Tunisia, English variations)
- Values: exact country name from ("Iran", "Morocco", "Tunisia", "Egypt", "Turkey") OR "_regional" for regions spanning multiple countries
- reject_places: places clearly OUTSIDE MENA (Central Asian, SE Asian, sub-Saharan places, etc.)
- signature_traditions: tradition tokens (lowercased) that unambiguously route without a place. E.g. "iznik" -> "Turkey", "beni ourain" -> "Morocco", "qashqai" -> "Iran"

Return ONLY the Python dict as a JSON object with keys "place_to_country", "reject_places" (list), "signature_traditions". No prose. Aim for ~40-70 place entries and ~10-20 signature traditions."""

proc = subprocess.run('claude --print --model claude-opus-5', shell=True,
                      input=prompt.encode('utf-8'), capture_output=True, timeout=180)
sys.stdout.write(proc.stdout.decode('utf-8', errors='replace'))
