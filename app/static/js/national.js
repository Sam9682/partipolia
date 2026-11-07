/* Gestion des erreurs HTMX des pages nationales (aucune dépendance externe).
 * - Les fragments d'erreur HTML (422 / 503) sont insérés tels quels.
 * - Toute autre erreur (429, réseau) affiche un message avec « Réessayer ».
 * - Le formulaire n'est jamais remplacé : la saisie est conservée. */
(function () {
  function errorHtml(message, retryTarget) {
    var box = document.createElement('div');
    box.className = 'rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800';
    box.setAttribute('role', 'alert');
    var p = document.createElement('p');
    p.textContent = message;
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn-outline mt-2';
    b.textContent = 'Réessayer';
    b.setAttribute('data-retry', retryTarget || '');
    box.appendChild(p);
    box.appendChild(b);
    return box;
  }

  function show(target, message, retryTarget) {
    if (!target) { return; }
    target.replaceChildren(errorHtml(message, retryTarget));
  }

  function retryTargetOf(evt) {
    var elt = evt.detail.elt;
    var form = elt && (elt.tagName === 'FORM' ? elt : elt.closest('form'));
    return form ? form.id : '';
  }

  document.addEventListener('htmx:beforeSwap', function (evt) {
    var xhr = evt.detail.xhr;
    var type = xhr.getResponseHeader('Content-Type') || '';
    if ((xhr.status === 409 || xhr.status === 422 || xhr.status === 503) && type.indexOf('text/html') === 0) {
      evt.detail.shouldSwap = true;
      evt.detail.isError = false;
    }
  });

  document.addEventListener('htmx:responseError', function (evt) {
    var status = evt.detail.xhr.status;
    var message = status === 429
      ? 'Trop de requêtes. Patientez un instant avant de réessayer.'
      : 'Le calcul est momentanément indisponible.';
    show(evt.detail.target, message, retryTargetOf(evt));
  });

  document.addEventListener('htmx:sendError', function (evt) {
    show(evt.detail.target, 'La connexion a échoué. Votre saisie est conservée.', retryTargetOf(evt));
  });

  document.addEventListener('click', function (evt) {
    var button = evt.target.closest('[data-retry]');
    if (!button) { return; }
    var form = document.getElementById(button.getAttribute('data-retry'));
    if (form && form.requestSubmit) { form.requestSubmit(); } else { window.location.reload(); }
  });

  document.addEventListener('click', function (evt) {
    var button = evt.target.closest('[data-preset]');
    if (!button) { return; }
    var preset = JSON.parse(button.getAttribute('data-preset'));
    var form = document.getElementById(button.getAttribute('data-form') || 'form-scenario');
    if (!form) { return; }
    var prefix = button.getAttribute('data-prefix') || '';
    form.elements[prefix + 'participation_rate'].value = preset.participation_rate;
    form.elements[prefix + 'horizon'].value = preset.horizon;
    Array.prototype.forEach.call(form.elements, function (el) {
      if (el.name && el.name.indexOf(prefix + 'red_') === 0) {
        var code = el.name.slice((prefix + 'red_').length);
        el.value = preset.reductions[code] !== undefined ? preset.reductions[code] : '';
      }
    });
  });
})();
