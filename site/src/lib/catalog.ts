// Labels and helpers shared by the home page and the catalog.

export const ART_FORMS: Record<string, string> = {
  textile: 'Textiles',
  garment: 'Garments',
  jewelry: 'Jewelry',
  ceramic: 'Ceramics',
  metalwork: 'Metalwork',
  arms: 'Arms & armour',
  sculpture: 'Sculpture',
  'masks-ritual': 'Masks & ritual',
  instruments: 'Instruments',
  architectural: 'Architecture',
  household: 'Household',
  'painting-mss': 'Paintings & manuscripts',
  photo: 'Museum photographs',
  commons: 'Wikimedia Commons photos',
};

export const REGIONS: Record<string, string> = {
  'sub-saharan-africa': 'Sub-Saharan Africa',
  'middle-east-north-africa': 'Middle East & North Africa',
  europe: 'Europe',
  caucasus: 'Caucasus',
  'central-asia': 'Central Asia',
  'south-asia': 'South Asia',
  'east-asia': 'East Asia',
  'southeast-asia': 'Southeast Asia',
  oceania: 'Oceania',
  'north-america': 'North America',
  'latin-america': 'Latin America',
};

// [title, culture key, art form, tradition, image, object id | null], see scripts/sync-public.mjs.
export type ImageRow = [string, string, string, string, string, string | null];

export const imageSrc = (image: string) =>
  image.startsWith('http') || image.startsWith('/') ? image : `/${image.replace(/\\/g, '/')}`;

export const artFormLabel = (k: string) => ART_FORMS[k] ?? k;
export const regionLabel = (k: string) => REGIONS[k] ?? k;
