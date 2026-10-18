// Mirrors goldbot/api/chart_layouts.py: the server checks again, this only gives the owner an answer before saving.
const LAYOUT_URL = /^https:\/\/www\.tradingview\.com\/chart\/(?:[A-Za-z0-9_-]{1,60}\/)?[A-Za-z0-9]{4,20}\/?$/;
export const validLayoutUrl = (u: string): boolean => LAYOUT_URL.test(u.trim());
