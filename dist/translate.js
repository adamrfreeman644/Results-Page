(() => {
  window.googleTranslateElementInit = () => {
    const host = document.getElementById("google_translate_element");
    if (!host || !window.google?.translate) return;
    new google.translate.TranslateElement({
      pageLanguage: "en",
      includedLanguages: "en,pt,es,fr,de,it,nl",
      autoDisplay: false,
    }, "google_translate_element");
  };
  const script = document.createElement("script");
  script.src = "https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit";
  script.async = true;
  document.head.appendChild(script);
})();
