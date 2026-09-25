export interface GlobePoint {
  key: string;
  region: string;
  country: string;
  ethnicity: string;
  homeland_place: string | null;
  lat: number;
  lon: number;
  object_count: number;
  seed_traditions: string[];
  top_image: string | null;
}

export interface SlimObject {
  id: string;
  title: string | null;
  date_text: string | null;
  art_form: string | null;
  tradition: string | null;
  pattern_density: number | null;
  source: string | null;
  object_url: string | null;
  image: string | null;
  place: string | null;
}

export interface CommonsPhoto {
  title: string | null;
  thumb_url: string | null;
  page_url: string | null;
  credit: string | null;
  license: string | null;
  description: string | null;
  source_category: string | null;
}

export interface UnescoIchEntry {
  code: string | null;             // e.g. "RL/00089"
  title: string | null;            // e.g. "shashmaqam"
  description: string | null;      // one-line summary from Wikidata
  unesco_url: string | null;       // https://ich.unesco.org/en/RL/00089
  commons_category: string | null; // for optional thumbnail lookup
}

export interface FolkwaysEntry {
  title: string | null;
  unit: string | null;             // "CFCHFOLKLIFE" | "SIL" | ...
  record_url: string | null;
}

export interface EthnicityShard {
  key: string;
  region: string;
  country: string;
  ethnicity: string;
  homeland: { lat: number; lon: number } | null;
  homeland_place: string | null;
  seed_traditions: string[];
  object_count: number;
  writeup_markdown: string | null;
  art_form_buckets: Record<string, SlimObject[]>;
  // media enrichment (optional — sidecars may be missing)
  wikipedia_url?: string | null;
  wikipedia_title?: string | null;
  commons_photos?: CommonsPhoto[];
  unesco_ich?: UnescoIchEntry[];
  folkways?: FolkwaysEntry[];
}
