/**
 * PAN Application Portal (Demo Replica) Shared Script
 * Fictional demo helper logic for Agastya Assist
 */
(function () {
  // Test mode support: ?demo_test=1 exposes window.__demoCaptchaAnswer for automation ONLY
  const params = new URLSearchParams(window.location.search);
  const isTest = params.get('demo_test') === '1';

  if (isTest && typeof CanvasRenderingContext2D !== 'undefined') {
    let captured = [];
    const origClearRect = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (x, y, w, h) {
      if (this.canvas && this.canvas.width === 140 && this.canvas.height === 44) {
        captured = [];
      }
      return origClearRect.apply(this, arguments);
    };

    const origFillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
      if (
        typeof text === 'string' &&
        text.length === 1 &&
        this.canvas &&
        this.canvas.width === 140 &&
        this.canvas.height === 44
      ) {
        captured.push(text);
        if (captured.length === 5) {
          window.__demoCaptchaAnswer = captured.join('');
        }
      }
      return origFillText.apply(this, arguments);
    };
  }

  // Preserve ?demo_test=1 on internal link clicks and form submits
  function preserveTestParam() {
    if (!isTest) return;
    document.querySelectorAll('a').forEach((link) => {
      const href = link.getAttribute('href');
      if (href && !href.startsWith('http') && !href.startsWith('#') && !href.startsWith('javascript:')) {
        const u = new URL(href, window.location.href);
        u.searchParams.set('demo_test', '1');
        link.setAttribute('href', u.pathname.split('/').pop() + u.search);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', preserveTestParam);
  } else {
    preserveTestParam();
  }
})();

// Fake Assessing Officer (AO) Code Dataset (~17 rows across Indian cities)
const PAN_AO_CODES = [
  { city: 'Delhi', desc: 'Ward 1(1), ITO Income Tax Office', area: 'DEL', type: 'W', range: '101', no: '1' },
  { city: 'Delhi', desc: 'Circle 2(2), Civic Centre Minto Road', area: 'DEL', type: 'C', range: '102', no: '2' },
  { city: 'Mumbai', desc: 'Ward 12(3), Aayakar Bhavan Marine Lines', area: 'MUM', type: 'W', range: '201', no: '3' },
  { city: 'Mumbai', desc: 'Circle 15(1), BKC Bandra-Kurla Complex', area: 'MUM', type: 'C', range: '202', no: '1' },
  { city: 'Bengaluru', desc: 'Ward 3(1), Koramangala Income Tax Complex', area: 'BLR', type: 'W', range: '301', no: '1' },
  { city: 'Bengaluru', desc: 'Circle 4(2), Queen’s Road Central Circle', area: 'BLR', type: 'C', range: '302', no: '2' },
  { city: 'Chennai', desc: 'Ward 5(1), Wanaparthy Block Nungambakkam', area: 'CHE', type: 'W', range: '401', no: '1' },
  { city: 'Kolkata', desc: 'Ward 8(2), Aayakar Bhawan Chowringhee', area: 'CAL', type: 'W', range: '501', no: '2' },
  { city: 'Hyderabad', desc: 'Ward 6(1), Aayakar Bhavan Basheerbagh', area: 'HYD', type: 'W', range: '601', no: '1' },
  { city: 'Pune', desc: 'Ward 4(1), PMT Building Swargate', area: 'PNE', type: 'W', range: '701', no: '1' },
  { city: 'Ahmedabad', desc: 'Ward 7(2), Pratyaksh Kar Bhavan Ambavadi', area: 'AHM', type: 'W', range: '801', no: '2' },
  { city: 'Jaipur', desc: 'Ward 2(1), NCR Building Statue Circle', area: 'JPR', type: 'W', range: '901', no: '1' },
  { city: 'Lucknow', desc: 'Ward 3(2), Pratyaksh Kar Bhawan Hazratganj', area: 'LKN', type: 'W', range: '951', no: '2' },
  { city: 'Chandigarh', desc: 'Ward 1(3), Sector 17 Central Revenue Bldg', area: 'CHD', type: 'W', range: '961', no: '3' },
  { city: 'Patna', desc: 'Ward 2(2), Lok Nayak Bhawan Dak Bungalow', area: 'PAT', type: 'W', range: '971', no: '2' },
  { city: 'Bhopal', desc: 'Ward 3(1), Aayakar Bhavan Hoshangabad Rd', area: 'BPL', type: 'W', range: '981', no: '1' },
  { city: 'Kochi', desc: 'Ward 1(1), CR Building IS Press Road', area: 'KOC', type: 'W', range: '991', no: '1' }
];

// Helper to get or save PAN Portal state
window.PanStorage = {
  get: function (key, def) {
    if (window.DemoPortal && window.DemoPortal.load) {
      const v = window.DemoPortal.load('pan_' + key);
      return v !== null ? v : def;
    }
    return def;
  },
  set: function (key, val) {
    if (window.DemoPortal && window.DemoPortal.save) {
      window.DemoPortal.save('pan_' + key, val);
    }
  }
};
