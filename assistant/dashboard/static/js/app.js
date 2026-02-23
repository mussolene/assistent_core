/**
 * Dashboard shared JS (ROADMAP 3.2): toast, apiPost.
 */
(function () {
  "use strict";

  var toastHideTimer = null;

  window.showToast = function (message, type) {
    type = type || "success";
    var container = document.getElementById("toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-container";
      document.body.appendChild(container);
    }
    var toast = document.createElement("div");
    toast.className = "toast toast-" + type;
    toast.textContent = message;
    container.appendChild(toast);
    if (toastHideTimer) clearTimeout(toastHideTimer);
    toastHideTimer = setTimeout(function () {
      toast.classList.add("toast-hide");
      setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 300);
      toastHideTimer = null;
    }, 3000);
  };

  /**
   * POST form as FormData, return JSON. Headers set for JSON response.
   */
  window.apiPostForm = function (url, formElement) {
    var formData = new FormData(formElement);
    return fetch(url, {
      method: "POST",
      body: formData,
      headers: {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    }).then(function (r) {
      if (r.status === 401) {
        window.location.href = "/login";
        throw new Error("Unauthorized");
      }
      return r.json();
    });
  };
})();
