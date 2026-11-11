/* Fonctions pures pour « Effacer tout » (bilan net).
 * Aucun accès à localStorage, sessionStorage ni document.cookie. */
(function (root) {
  'use strict';

  var NON_REDUCTIBLE_LINES = ['nourriture', 'electricite', 'essence'];

  function emptyForm(aidCodes) {
    var aides = {};
    (aidCodes || []).forEach(function (code) {
      // modifie:true empêche tout préremplissage automatique après l'effacement.
      aides[code] = { valeur: '', modifie: true };
    });
    var nonReductible = {};
    NON_REDUCTIBLE_LINES.forEach(function (ligne) {
      nonReductible[ligne] = { valeur: '0', modifie: true };
    });
    return {
      statut: null,
      revenu: '',
      enfants: '0',
      aides: aides,
      nonMonetaires: [],
      immobilier: { loyers: '', taux: '', tauxModifie: false, tauxEfface: true },
      nonReductible: nonReductible,
    };
  }

  function emptyConsumption(categoryCodes) {
    var reductions = {};
    var overrides = {};
    (categoryCodes || []).forEach(function (c) {
      reductions[c] = '0';
      overrides[c] = '0';
    });
    return { reductions: reductions, adultes: 1, reference_overrides: overrides };
  }

  function emptyUiState() {
    return { erreurs: {}, erreursGenerales: [], erreur: '', pastilleEstimation: {} };
  }

  function contributionLine(total, repli) {
    return typeof total === 'string' && total !== '' ? total : repli;
  }

  function nextTaux(immobilier, defaut) {
    if (immobilier.tauxEfface) return immobilier.taux;
    if (defaut !== undefined && defaut !== null && defaut !== '' && !immobilier.tauxModifie) {
      return defaut;
    }
    return immobilier.taux;
  }

  function onRevenuInput(immobilier) {
    immobilier.tauxEfface = false;
    return immobilier;
  }

  var api = {
    emptyForm: emptyForm,
    emptyConsumption: emptyConsumption,
    emptyUiState: emptyUiState,
    contributionLine: contributionLine,
    nextTaux: nextTaux,
    onRevenuInput: onRevenuInput,
  };

  if (root) root.PartipoliaClear = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : null);
