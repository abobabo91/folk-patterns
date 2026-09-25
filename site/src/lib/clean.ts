// Display-layer sanitizers for raw source data. Museum APIs give us all
// sorts of shapes (LangAware {def: '...'} dicts from Europeana, HTML tags
// inline in V&A descriptions, semantic-web concept URIs, filenames with
// extensions). Everywhere we render user-facing text or a title, run it
// through the appropriate helper here so bugs like `[object Object]` or
// `<i>khilat</i>` don't leak to the UI.

const CONCEPT_URI_RE = /^https?:\/\/(?:data|www)\.europeana\.eu\/concept\//i;
const FILE_EXT_RE = /\.(jpe?g|png|tif{1,2}|gif|webp|bmp)$/i;

/** LangAware dict → first plausible string. `{def: '19. Jahrhundert'}` → `'19. Jahrhundert'`.
 *  Arrays get their first element unwrapped. Anything already a string passes through. */
export function unwrapLang(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.length ? unwrapLang(v[0]) : '';
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>;
    for (const lang of ['en', 'def', 'nl', 'de', 'fr', 'sv']) {
      if (lang in o) return unwrapLang(o[lang]);
    }
    // Fallback: first value in the object
    for (const val of Object.values(o)) {
      const s = unwrapLang(val);
      if (s) return s;
    }
  }
  return '';
}

/** Strip a trailing image extension from a filename-style title.
 *  `A common Balinese motif.jpg` → `A common Balinese motif`. */
export function stripExt(s: string): string {
  return s.replace(FILE_EXT_RE, '');
}

/** True if the string looks like a Europeana semantic-web concept URI.
 *  Used to filter out `materials: [http://data.europeana.eu/concept/2088]`. */
export function isConceptURI(s: string): boolean {
  return CONCEPT_URI_RE.test(s.trim());
}

/** Filter concept URIs from a list, return only the human-readable strings. */
export function cleanList(vals: unknown): string[] {
  if (!Array.isArray(vals)) return [];
  return vals
    .map((v) => unwrapLang(v))
    .filter((s) => s && !isConceptURI(s));
}

/** For medium_raw which comes as either a string (with `; ` separators) or an array.
 *  If every part is a concept URI, return empty string so caller can hide the row. */
export function cleanMediumRaw(v: unknown): string {
  const s = unwrapLang(v);
  if (!s) return '';
  const parts = s.split(/\s*[;,]\s*/).filter((p) => p && !isConceptURI(p));
  return parts.join('; ');
}

/** For display: title + fallback + extension strip. */
export function displayTitle(title: unknown, fallback = ''): string {
  return stripExt(unwrapLang(title) || fallback).trim();
}

/** Return the best-effort location string. If it's clearly the museum's country
 *  and not the object's origin (heuristic: matches one of the aggregator-country
 *  denylist), return empty so caller can hide it. Prefer real place info from
 *  tradition + made_in_place; drop bare "Netherlands" for an Indonesian object. */
const MUSEUM_COUNTRY_DENYLIST = new Set([
  'Netherlands', 'Nederland', 'Belgium', 'België', 'Germany', 'Deutschland',
  'Sweden', 'Sverige', 'France', 'United Kingdom', 'Estonia', 'Czech Republic',
  'Austria', 'Denmark', 'Danmark',
]);
export function cleanMadeInPlace(v: unknown, objectCountry?: string): string {
  const s = unwrapLang(v);
  if (!s) return '';
  // If the reported place is a European aggregator country AND the object's
  // ethnicity/country is elsewhere, it's almost certainly the museum's country
  // (not where the object was made). Hide it.
  if (
    objectCountry &&
    MUSEUM_COUNTRY_DENYLIST.has(s) &&
    !s.toLowerCase().includes(objectCountry.toLowerCase())
  ) {
    return '';
  }
  return s;
}
