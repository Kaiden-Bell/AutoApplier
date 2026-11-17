// schema.org JobPosting extraction from <script type="application/ld+json"> tags.
//
// Injected into the page (isolated world) on demand, possibly more than once, so it only uses
// function declarations (safe to redeclare). The pure parsing helpers are also exported for
// Node tests (tests/jsonld.test.js).

var AA_MAX_DESCRIPTION = 45000;

function aaToArray(value) {
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value : [value];
}

function aaIsJobPosting(node) {
  return !!node && typeof node === "object" &&
    aaToArray(node["@type"]).some(function (t) { return /(^|[:/])JobPosting$/.test(String(t)); });
}

// Depth-first search through arrays, @graph containers and nested objects.
function aaFindJobPosting(data, depth) {
  depth = depth || 0;
  if (!data || typeof data !== "object" || depth > 8) return null;
  if (Array.isArray(data)) {
    for (var i = 0; i < data.length; i++) {
      var found = aaFindJobPosting(data[i], depth + 1);
      if (found) return found;
    }
    return null;
  }
  if (aaIsJobPosting(data)) return data;
  var keys = Object.keys(data);
  for (var k = 0; k < keys.length; k++) {
    var child = data[keys[k]];
    if (child && typeof child === "object") {
      var hit = aaFindJobPosting(child, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}

function aaDecodeEntities(text) {
  var named = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, function (m, code) {
    if (code[0] === "#") {
      var n = code[1] === "x" || code[1] === "X" ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return isNaN(n) ? m : String.fromCodePoint(n);
    }
    return Object.prototype.hasOwnProperty.call(named, code.toLowerCase()) ? named[code.toLowerCase()] : m;
  });
}

// Converts a (possibly entity-encoded) HTML description to plain text with line breaks.
function aaHtmlToText(html) {
  var text = String(html || "");
  for (var pass = 0; pass < 2; pass++) {
    if (!/<[a-z!/][^>]*>/i.test(text) && !/&[#a-z0-9]+;/i.test(text)) break;
    text = text
      .replace(/<(br|\/p|\/h[1-6]|\/div|\/tr|\/ul|\/ol)\b[^>]*>/gi, "\n")
      .replace(/<li\b[^>]*>/gi, "\n- ")
      .replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, "")
      .replace(/<[^>]+>/g, "");
    text = aaDecodeEntities(text);
  }
  return text
    .replace(/[ \t ]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function aaFormatLocation(posting) {
  var parts = [];
  aaToArray(posting.jobLocation).forEach(function (loc) {
    var addr = loc && typeof loc === "object" && loc.address !== undefined ? loc.address : loc;
    if (typeof addr === "string") {
      if (addr.trim()) parts.push(addr.trim());
    } else if (addr && typeof addr === "object") {
      var country = addr.addressCountry && typeof addr.addressCountry === "object"
        ? addr.addressCountry.name : addr.addressCountry;
      var line = [addr.addressLocality, addr.addressRegion, country]
        .filter(function (x) { return typeof x === "string" && x.trim(); })
        .join(", ");
      if (line) parts.push(line);
    }
  });
  var remote = aaToArray(posting.jobLocationType).some(function (t) { return /telecommute|remote/i.test(String(t)); });
  if (remote) parts.unshift("Remote");
  return parts.filter(function (p, i) { return parts.indexOf(p) === i; }).join("; ");
}

var AA_UNITS = { HOUR: "hour", DAY: "day", WEEK: "week", MONTH: "month", YEAR: "year" };

function aaFormatAmount(value, symbol) {
  var n = Number(value);
  if (!isFinite(n)) return String(value);
  return symbol + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function aaFormatPay(posting) {
  var salary = aaToArray(posting.baseSalary !== undefined ? posting.baseSalary : posting.estimatedSalary)[0];
  if (salary === undefined || salary === null || salary === "") return "";
  if (typeof salary !== "object") return String(salary);

  var currency = salary.currency || salary.salaryCurrency || "";
  var symbol = !currency || currency === "USD" ? "$" : currency + " ";
  var value = salary.value !== undefined ? salary.value : salary;
  var unit = salary.unitText || "";
  var min, max;
  if (value && typeof value === "object") {
    min = value.minValue !== undefined ? value.minValue : value.value;
    max = value.maxValue;
    unit = value.unitText || unit;
  } else {
    min = value;
  }
  if ((min === undefined || min === null) && (max === undefined || max === null)) return "";

  var range = min !== undefined && min !== null ? aaFormatAmount(min, symbol) : "";
  if (max !== undefined && max !== null && String(max) !== String(min)) {
    range = range ? range + " - " + aaFormatAmount(max, symbol) : aaFormatAmount(max, symbol);
  }
  var unitWord = AA_UNITS[String(unit).toUpperCase()] || (unit ? String(unit).toLowerCase() : "");
  return unitWord ? range + " / " + unitWord : range;
}

// Maps a schema.org JobPosting object to the backend's Job fields.
function parseJobPosting(posting) {
  if (!aaIsJobPosting(posting)) return null;
  var org = posting.hiringOrganization;
  var company = org && typeof org === "object" ? org.name : org;
  var description = aaHtmlToText(posting.description);
  if (company && description.indexOf(company) === -1) description = "Company: " + company + "\n\n" + description;
  return {
    title: aaHtmlToText(posting.title || posting.name || ""),
    location: aaFormatLocation(posting),
    pay: aaFormatPay(posting),
    description: description.slice(0, AA_MAX_DESCRIPTION),
    url: typeof posting.url === "string" ? posting.url : "",
  };
}

// Reads every JSON-LD block on the current page and returns the first JobPosting found.
function extractJobPostingFromJsonLd(doc) {
  doc = doc || document;
  var scripts = doc.querySelectorAll('script[type="application/ld+json"]');
  for (var i = 0; i < scripts.length; i++) {
    var data;
    try {
      data = JSON.parse(scripts[i].textContent);
    } catch (e) {
      continue;
    }
    var posting = aaFindJobPosting(data);
    if (posting) {
      var job = parseJobPosting(posting);
      if (job && (job.title || job.description)) return job;
    }
  }
  return null;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { parseJobPosting: parseJobPosting, aaFindJobPosting: aaFindJobPosting, aaHtmlToText: aaHtmlToText, aaFormatPay: aaFormatPay, aaFormatLocation: aaFormatLocation };
}
