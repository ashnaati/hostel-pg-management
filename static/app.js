// Kept in a file (not inline) so the Content-Security-Policy can forbid inline scripts.
document.addEventListener("click", function (event) {
  if (event.target.closest("[data-print]")) window.print();
});
