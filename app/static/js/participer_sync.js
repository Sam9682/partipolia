/*
 * Synchronisation Postgres / navigateur pour /participer et /simulateur-national.
 * Fonctions pures, sans DOM ni appel réseau.
 */
(function (root) {
  'use strict';

  var KEY = 'partipolia.referenceOverrides';
  var MAX_AMOUNT = 100000;
  var AMOUNT_RE = /^\d+(\.\d{1,2})?$/;

  // Montant_Valide : "123", "123.4" ou "123.45", > 0 et <= 100000.
  function isValidAmount(value) {
    if (typeof value !== 'string' || !AMOUNT_RE.test(value)) { return false; }
    var n = Number(value);
    return n > 0 && n <= MAX_AMOUNT;
  }

  // Lecture seule. Ne lève jamais. Retourne { code: "montant" } filtré.
  function readReferenceOverrides(storage, knownCodes) {
    try {
      var raw = storage.getItem(KEY);
      if (typeof raw !== 'string') { return {}; }
      var data = JSON.parse(raw);
      if (data === null || typeof data !== 'object' || Array.isArray(data)) { return {}; }
      var out = {};
      (knownCodes || []).forEach(function (code) {
        if (Object.prototype.hasOwnProperty.call(data, code) && isValidAmount(data[code])) {
          out[code] = data[code];
        }
      });
      return out;
    } catch (e) {
      return {};
    }
  }

  // L'API prime ; une valeur absente ou null conserve la valeur par défaut.
  function mergeProfile(defaults, account, citizen) {
    var p = Object.assign({}, defaults);
    if (account && typeof account.display_name === 'string') { p.pseudonym = account.display_name; }
    if (citizen) {
      if (typeof citizen.region === 'string') { p.region = citizen.region; }
      if (Number.isInteger(citizen.household_adults)) { p.adults = citizen.household_adults; }
      if (typeof citizen.consent_aggregation === 'boolean') { p.consent = citizen.consent_aggregation; }
    }
    return p;
  }

  // Un pourcentage par catégorie connue ; 0 par défaut ; codes inconnus ignorés.
  function buildPlan(categories, commitments) {
    var plan = {};
    (categories || []).forEach(function (c) { plan[c.code] = 0; });
    (commitments || []).forEach(function (c) {
      if (c && Object.prototype.hasOwnProperty.call(plan, c.category_code)) {
        plan[c.category_code] = Number(c.reduction_percentage);
      }
    });
    return plan;
  }

  var api = {
    KEY: KEY,
    MAX_AMOUNT: MAX_AMOUNT,
    isValidAmount: isValidAmount,
    readReferenceOverrides: readReferenceOverrides,
    mergeProfile: mergeProfile,
    buildPlan: buildPlan
  };

  root.PartipoliaSync = Object.assign(root.PartipoliaSync || {}, api);
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = root.PartipoliaSync;
  }
})(typeof window !== 'undefined' ? window : globalThis);
