/*
 * Curseur de population pour la simulation nationale.
 * Fonctions pures : arithmétique décimale exacte (BigInt), formatage fr-FR.
 * Aucun appel réseau.
 */
(function (root) {
  'use strict';

  var MAX_COUNT = 68000000;
  var SLIDER_MAX = 1000;
  var SCALE = 6;
  var ABS_LIMIT = 1000000000;
  var STORAGE_KEY = 'partipolia.nationalTransfer';
  var HORIZONS = ['short', 'medium', 'long'];

  var FACTOR = 10n ** BigInt(SCALE);
  var DECIMAL_RE = /^-?\d+(\.\d+)?$/;
  var THIN_NBSP = '\u202f';
  var NBSP = '\u00a0';
  var MINUS = '\u2212';

  // --- Curseur -------------------------------------------------------------

  function sliderToCount(position) {
    var p = Number(position);
    if (!Number.isFinite(p)) { p = 0; }
    p = Math.min(Math.max(p, 0), SLIDER_MAX);
    if (p >= SLIDER_MAX) { return MAX_COUNT; }
    var n = Math.round(Math.pow(MAX_COUNT, p / SLIDER_MAX));
    return Math.min(Math.max(n, 1), MAX_COUNT);
  }

  function countToSlider(count) {
    var n = Number(count);
    if (!Number.isFinite(n) || n <= 1) { return 0; }
    if (n >= MAX_COUNT) { return SLIDER_MAX; }
    var p = Math.round(SLIDER_MAX * Math.log(n) / Math.log(MAX_COUNT));
    return Math.min(Math.max(p, 0), SLIDER_MAX);
  }

  // --- Arithmétique décimale exacte ---------------------------------------

  // Division entière arrondie au plus proche, demi vers l'infini en valeur absolue.
  function divRound(num, den) {
    var negative = num < 0n;
    var abs = negative ? -num : num;
    var q = (abs * 2n + den) / (den * 2n);
    return negative ? -q : q;
  }

  function decimalToScaled(text) {
    if (typeof text !== 'string' || !DECIMAL_RE.test(text)) { return null; }
    var negative = text.charAt(0) === '-';
    var body = negative ? text.slice(1) : text;
    var parts = body.split('.');
    var intPart = BigInt(parts[0]);
    var frac = parts.length > 1 ? parts[1] : '';
    var result;
    if (frac.length <= SCALE) {
      while (frac.length < SCALE) { frac += '0'; }
      result = intPart * FACTOR + BigInt(frac);
    } else {
      var digits = BigInt(parts[0] + frac);
      result = divRound(digits, 10n ** BigInt(frac.length - SCALE));
    }
    return negative ? -result : result;
  }

  function multiplyCount(count, scaled) {
    return divRound(BigInt(count) * BigInt(scaled), FACTOR);
  }

  // --- Formatage fr-FR -----------------------------------------------------

  function groupDigits(digits) {
    return digits.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_NBSP);
  }

  function formatCount(count) {
    var n = BigInt(count);
    var negative = n < 0n;
    var text = groupDigits((negative ? -n : n).toString());
    return (negative ? MINUS : '') + text;
  }

  function formatEuroSigned(euros) {
    var n = BigInt(euros);
    var negative = n < 0n;
    var text = groupDigits((negative ? -n : n).toString());
    return (negative ? MINUS : '+') + text + NBSP + '\u20ac';
  }

  // --- Transfert de session -----------------------------------------------

  function scaledToDecimal(scaled) {
    var negative = scaled < 0n;
    var abs = negative ? -scaled : scaled;
    var digits = abs.toString();
    while (digits.length <= SCALE) { digits = '0' + digits; }
    var text = digits.slice(0, digits.length - SCALE) + '.' + digits.slice(digits.length - SCALE);
    return (negative && abs !== 0n ? '-' : '') + text;
  }

  function boundToText(value) {
    if (typeof value === 'string') { return value; }
    if (typeof value === 'number' && Number.isFinite(value)) { return String(value); }
    return null;
  }

  function buildTransfer(sim) {
    try {
      if (!sim || !sim.fiscal || !sim.personal) { return null; }
      var impact = sim.fiscal.net_public_impact;
      if (!impact) { return null; }
      var adults = sim.personal.adults;
      if (typeof adults !== 'number' || !Number.isInteger(adults) || adults < 1 || adults > 10) {
        return null;
      }
      var lowScaled = decimalToScaled(boundToText(impact.low));
      var highScaled = decimalToScaled(boundToText(impact.high));
      if (lowScaled === null || highScaled === null) { return null; }
      var divisor = BigInt(adults);
      var horizon = sim.fiscal.horizon;
      return {
        low: scaledToDecimal(divRound(lowScaled, divisor)),
        high: scaledToDecimal(divRound(highScaled, divisor)),
        horizon: HORIZONS.indexOf(horizon) >= 0 ? horizon : 'medium'
      };
    } catch (e) {
      return null;
    }
  }

  function withinLimit(text) {
    var scaled = decimalToScaled(text);
    if (scaled === null) { return false; }
    var abs = scaled < 0n ? -scaled : scaled;
    return abs <= BigInt(ABS_LIMIT) * FACTOR;
  }

  function parseTransfer(raw) {
    if (typeof raw !== 'string') { return null; }
    var data;
    try { data = JSON.parse(raw); } catch (e) { return null; }
    if (!data || typeof data !== 'object' || Array.isArray(data)) { return null; }
    if (typeof data.low !== 'string' || typeof data.high !== 'string') { return null; }
    if (!withinLimit(data.low) || !withinLimit(data.high)) { return null; }
    var horizon = HORIZONS.indexOf(data.horizon) >= 0 ? data.horizon : 'medium';
    return { low: data.low, high: data.high, horizon: horizon };
  }

  function writeTransfer(storage, sim) {
    var transfer = buildTransfer(sim);
    if (transfer === null) { return false; }
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(transfer));
      return true;
    } catch (e) {
      return false;
    }
  }

  function readTransfer(storage) {
    try {
      var raw = storage.getItem(STORAGE_KEY);
      if (raw === null || raw === undefined) { return null; }
      var transfer = parseTransfer(raw);
      if (transfer === null) { storage.removeItem(STORAGE_KEY); }
      return transfer;
    } catch (e) {
      return null;
    }
  }

  var api = {
    buildTransfer: buildTransfer,
    parseTransfer: parseTransfer,
    writeTransfer: writeTransfer,
    readTransfer: readTransfer,
    MAX_COUNT: MAX_COUNT,
    SLIDER_MAX: SLIDER_MAX,
    SCALE: SCALE,
    ABS_LIMIT: ABS_LIMIT,
    STORAGE_KEY: STORAGE_KEY,
    HORIZONS: HORIZONS,
    sliderToCount: sliderToCount,
    countToSlider: countToSlider,
    decimalToScaled: decimalToScaled,
    multiplyCount: multiplyCount,
    formatCount: formatCount,
    formatEuroSigned: formatEuroSigned
  };

  root.PartipoliaNational = Object.assign(root.PartipoliaNational || {}, api);
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = root.PartipoliaNational;
  }
})(typeof window !== 'undefined' ? window : globalThis);
