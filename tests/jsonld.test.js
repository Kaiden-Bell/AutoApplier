// Run with: node --test tests/
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseJobPosting, aaFindJobPosting, aaHtmlToText, aaFormatPay } = require("../extension/jsonld.js");

test("Greenhouse-style posting with HTML-entity description and salary range", () => {
  const posting = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    title: "Backend Software Engineer",
    description: "&lt;p&gt;Build &lt;strong&gt;APIs&lt;/strong&gt;.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;",
    hiringOrganization: { "@type": "Organization", name: "Acme" },
    jobLocation: { "@type": "Place", address: { addressLocality: "Reno", addressRegion: "NV", addressCountry: "US" } },
    baseSalary: { "@type": "MonetaryAmount", currency: "USD", value: { "@type": "QuantitativeValue", minValue: 85000, maxValue: 105000, unitText: "YEAR" } },
  };
  const job = parseJobPosting(posting);
  assert.equal(job.title, "Backend Software Engineer");
  assert.equal(job.location, "Reno, NV, US");
  assert.equal(job.pay, "$85,000 - $105,000 / year");
  assert.match(job.description, /^Company: Acme/);
  assert.match(job.description, /Build APIs\./);
  assert.match(job.description, /- Python\n- SQL/);
  assert.doesNotMatch(job.description, /<|&lt;/);
});

test("finds a JobPosting inside @graph and with an array @type", () => {
  const data = { "@context": "https://schema.org", "@graph": [
    { "@type": "WebPage", name: "Careers" },
    { "@type": ["Thing", "JobPosting"], title: "Firmware Engineer", description: "Write firmware." },
  ] };
  const found = aaFindJobPosting(data);
  assert.equal(found.title, "Firmware Engineer");
  assert.equal(aaFindJobPosting([{ "@type": "Organization" }]), null);
});

test("remote postings and hourly pay", () => {
  const job = parseJobPosting({
    "@type": "JobPosting", title: "Web Developer Intern", description: "x",
    jobLocationType: "TELECOMMUTE",
    applicantLocationRequirements: { "@type": "Country", name: "USA" },
    baseSalary: { currency: "USD", value: { value: 32.5, unitText: "HOUR" } },
  });
  assert.equal(job.location, "Remote");
  assert.equal(job.pay, "$32.5 / hour");
});

test("pay edge cases", () => {
  assert.equal(aaFormatPay({}), "");
  assert.equal(aaFormatPay({ baseSalary: "Competitive" }), "Competitive");
  assert.equal(aaFormatPay({ baseSalary: { currency: "EUR", value: 50000, unitText: "YEAR" } }), "EUR 50,000 / year");
  assert.equal(aaFormatPay({ baseSalary: [{ currency: "USD", value: { minValue: 20, maxValue: 20, unitText: "HOUR" } }] }), "$20 / hour");
});

test("non-postings return null and plain text is preserved", () => {
  assert.equal(parseJobPosting({ "@type": "Organization" }), null);
  assert.equal(aaHtmlToText("R&D role, 5+ years"), "R&D role, 5+ years");
});
