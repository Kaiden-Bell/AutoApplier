// Injected after jsonld.js when the user clicks "Extract posting". The value of the final
// expression is returned to the popup by chrome.scripting.executeScript.
//
// Tier 1: schema.org JSON-LD. Tier 2: page heuristics the user confirms/edits in the popup.

(function () {
  var MAX_TEXT = 20000;

  function clean(text) {
    return String(text || "").replace(/[ \t ]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  }

  function guessPay(text) {
    var m = text.match(/\$\s?\d[\d,]*(?:\.\d+)?\s*k?(?:\s*(?:-|–|to)\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*k?)?(?:\s*(?:\/|per|an|a)\s*(?:hour|hr|year|yr|annum|month))?/i);
    return m ? m[0].replace(/\s+/g, " ").trim() : "";
  }

  var job = null;
  try {
    job = extractJobPostingFromJsonLd(document);
  } catch (e) {
    job = null;
  }
  if (job) {
    if (!job.url) job.url = location.href;
    return { source: "jsonld", job: job };
  }

  var selection = clean(window.getSelection ? window.getSelection().toString() : "");
  var main = document.querySelector("main, [role=main], article");
  var bodyText = clean((main && main.innerText.length > 500 ? main : document.body).innerText);
  var description = (selection.length > 200 ? selection : bodyText).slice(0, MAX_TEXT);
  var h1 = document.querySelector("h1");

  return {
    source: "heuristic",
    job: {
      title: clean(h1 ? h1.innerText : document.title).slice(0, 300),
      location: "",
      pay: guessPay(description),
      description: description,
      url: location.href,
    },
  };
})();
