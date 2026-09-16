(function () {
  "use strict";

  // Menu burger pour petits écrans
  var burgerButton = document.getElementById("burger-button");
  var navMenu = document.getElementById("nav-menu");

  function closeMenu() {
    if (navMenu) {
      navMenu.classList.remove("active");
    }
  }

  function toggleMenu() {
    if (navMenu) {
      navMenu.classList.toggle("active");
    }
  }

  if (burgerButton) {
    burgerButton.addEventListener("click", toggleMenu);
  }

  // Fermer le menu quand on clique sur un lien (mobile)
  if (navMenu) {
    var navLinks = navMenu.querySelectorAll("a");
    navLinks.forEach(function(link) {
      link.addEventListener("click", closeMenu);
    });
  }
})();
