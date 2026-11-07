/* Soutien HTMX des Revendications (aucune dépendance externe).
 * - Toute réponse en erreur (4xx / 5xx), erreur réseau ou délai > 10 s est affichée
 *   dans la zone d'erreur du bloc, en français ; le compteur et le bouton restent inchangés.
 * - Sans JavaScript, le formulaire classique (redirection 303) prend le relais. */
(function () {
  if (window.htmx) { window.htmx.config.timeout = 10000; }

  function showError(id, message, formId) {
    var old = id && document.getElementById(id);
    if (!old) { return; }
    var p = document.createElement('p');
    p.id = id;
    p.className = 'w-full rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-800';
    p.setAttribute('role', 'alert');
    p.appendChild(document.createTextNode(message + ' '));
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn-outline ml-2';
    b.textContent = 'Réessayer';
    b.setAttribute('data-retry', formId || '');
    p.appendChild(b);
    old.replaceWith(p);
  }

  function formOf(evt) {
    var elt = (evt.detail.requestConfig && evt.detail.requestConfig.elt) || evt.detail.elt;
    return elt && (elt.tagName === 'FORM' ? elt : elt.closest('form'));
  }

  function messageFor(status) {
    if (status === 401) { return 'Connectez-vous avec un compte citoyen pour effectuer cette action.'; }
    if (status === 429) { return 'Trop de requêtes. Patientez un instant avant de réessayer.'; }
    return 'L\u2019action a échoué. L\u2019état affiché est inchangé.';
  }

  document.addEventListener('htmx:beforeSwap', function (evt) {
    var xhr = evt.detail.xhr;
    if (xhr.status < 400) { return; }
    var form = formOf(evt);
    var errorId = form && form.getAttribute('data-error-target');
    if (!errorId) { return; }
    var type = xhr.getResponseHeader('Content-Type') || '';
    if (type.indexOf('text/html') === 0 && document.getElementById(errorId)) {
      /* Le fragment d'erreur porte le même id : il remplace la zone d'erreur seule. */
      evt.detail.target = document.getElementById(errorId);
      evt.detail.shouldSwap = true;
      evt.detail.isError = false;
    } else {
      evt.detail.shouldSwap = false;
      showError(errorId, messageFor(xhr.status), form.id);
    }
  });

  function networkError(evt, message) {
    var form = formOf(evt);
    if (form) { showError(form.getAttribute('data-error-target'), message, form.id); }
  }
  document.addEventListener('htmx:sendError', function (evt) {
    networkError(evt, 'La connexion a échoué. L\u2019état affiché est inchangé.');
  });
  document.addEventListener('htmx:timeout', function (evt) {
    networkError(evt, 'Le serveur met trop de temps à répondre. L\u2019état affiché est inchangé.');
  });

  document.addEventListener('click', function (evt) {
    var button = evt.target.closest('[data-retry]');
    if (!button) { return; }
    var form = document.getElementById(button.getAttribute('data-retry'));
    if (form && form.requestSubmit) { form.requestSubmit(); } else { window.location.reload(); }
  });
})();
