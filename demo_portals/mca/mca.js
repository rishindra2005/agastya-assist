/**
 * Corporate Affairs Portal (Demo Replica) client logic.
 * Fictional demo helpers for SPICe+ Part A:
 * Dependent dropdowns, NIC search modal, proposed name availability check,
 * 300-char counter, auto-check, and submission with OTP and Captcha.
 */

// 1. Automation test bridge when ?demo_test=1 is present
(function initTestCaptchaBridge() {
  if (new URLSearchParams(window.location.search).get('demo_test') === '1') {
    const origFillText = CanvasRenderingContext2D.prototype.fillText;
    let charBuffer = '';
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y) {
      if (this.canvas && this.canvas.closest && this.canvas.closest('.demo-captcha')) {
        charBuffer += text;
        if (charBuffer.length === 5) {
          window.__demoCaptchaAnswer = charBuffer;
          charBuffer = '';
        }
      }
      return origFillText.apply(this, arguments);
    };
  }
})();

// 2. Dependent company classification data
const MCA_COMPANY_TYPES = {
  "private": {
    name: "Private Company",
    classes: ["Private"],
    categories: ["Company limited by shares", "Company limited by guarantee", "Unlimited company"],
    subcategories: ["Non-government company", "Subsidiary of company incorporated outside India"]
  },
  "opc": {
    name: "Private (One Person Company)",
    classes: ["Private"],
    categories: ["Company limited by shares", "Company limited by guarantee", "Unlimited company"],
    subcategories: ["Non-government company"]
  },
  "public": {
    name: "Public Company",
    classes: ["Public"],
    categories: ["Company limited by shares", "Company limited by guarantee", "Unlimited company"],
    subcategories: ["Non-government company", "Union government company", "State government company"]
  },
  "producer": {
    name: "Producer Company",
    classes: ["Private", "Public"],
    categories: ["Company limited by shares"],
    subcategories: ["Non-government company"]
  },
  "section8": {
    name: "Section 8 Company",
    classes: ["Private", "Public"],
    categories: ["Company limited by guarantee", "Company limited by shares"],
    subcategories: ["Non-government company"]
  }
};

// 3. Fake 21 NIC industrial division codes
const MCA_NIC_DIVISIONS = [
  { code: "01", title: "Crop and animal production, hunting and related service activities" },
  { code: "10", title: "Manufacture of food products" },
  { code: "13", title: "Manufacture of textiles" },
  { code: "21", title: "Manufacture of basic pharmaceutical products and pharmaceutical preparations" },
  { code: "26", title: "Manufacture of computer, electronic and optical products" },
  { code: "35", title: "Electricity, gas, steam and air conditioning supply" },
  { code: "41", title: "Construction of buildings" },
  { code: "43", title: "Specialised construction activities" },
  { code: "47", title: "Retail trade, except of motor vehicles and motorcycles" },
  { code: "49", title: "Land transport and transport via pipelines" },
  { code: "55", title: "Accommodation activities" },
  { code: "56", title: "Food and beverage service activities" },
  { code: "58", title: "Publishing activities" },
  { code: "61", title: "Telecommunications" },
  { code: "62", title: "Computer programming, consultancy and related activities" },
  { code: "64", title: "Financial service activities, except insurance and pension funding" },
  { code: "69", title: "Legal and accounting activities" },
  { code: "70", title: "Activities of head offices; management consultancy activities" },
  { code: "72", title: "Scientific research and development" },
  { code: "85", title: "Education" },
  { code: "86", title: "Human health activities" }
];

// 4. Fake existing company registry for availability collisions
const EXISTING_COMPANIES = [
  "TATA CONSULTANCY SERVICES",
  "INFOSYS",
  "INFOSYS LIMITED",
  "RELIANCE INDUSTRIES",
  "WIPRO",
  "WIPRO LIMITED",
  "HINDUSTAN UNILEVER",
  "BHARTI AIRTEL",
  "HDFC BANK",
  "ICICI BANK",
  "STATE BANK OF INDIA",
  "LARSEN AND TOUBRO"
];

function checkNameAvailability(rawName) {
  if (!rawName || !rawName.trim()) {
    return { status: "empty", message: "" };
  }
  const clean = rawName.trim().toUpperCase();

  // Rule 1: contains 'Bharat' or 'National' -> requires central government approval
  if (/\bBHARAT\b/i.test(clean) || /\bNATIONAL\b/i.test(clean) || clean.includes("BHARAT") || clean.includes("NATIONAL")) {
    return {
      status: "approval",
      message: "requires central government approval (Rule 8: words indicating government patronage)"
    };
  }

  // Normalize name for collision checking (remove Pvt, Ltd, Private Limited, punctuation)
  const norm = clean
    .replace(/\b(PVT|PRIVATE|LTD|LIMITED|LLP|INC|CORP)\b/gi, "")
    .replace(/[^A-Z0-9\s]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  for (const existing of EXISTING_COMPANIES) {
    const existingNorm = existing
      .replace(/\b(PVT|PRIVATE|LTD|LIMITED|LLP|INC|CORP)\b/gi, "")
      .replace(/[^A-Z0-9\s]/g, "")
      .replace(/\s+/g, " ")
      .trim();

    if (norm === existingNorm || clean === existing) {
      return {
        status: "unavailable",
        message: `name not available (Identical or too nearly resembles existing company: ${existing})`
      };
    }
  }

  return {
    status: "available",
    message: "name available for reservation"
  };
}

window.MCA_COMPANY_TYPES = MCA_COMPANY_TYPES;
window.MCA_NIC_DIVISIONS = MCA_NIC_DIVISIONS;
window.checkNameAvailability = checkNameAvailability;
