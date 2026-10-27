/* Income Tax e-Filing (Demo Replica) Shared JavaScript */
(function() {
  // 1. Support for ?demo_test=1: capture captcha answer for test automation
  const isDemoTest = new URLSearchParams(window.location.search).get('demo_test') === '1';
  if (isDemoTest) {
    let capturedChars = [];
    const origFillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function(text, x, y, maxW) {
      if (typeof text === 'string' && text.length === 1) {
        capturedChars.push(text);
        if (capturedChars.length === 5) {
          window.__demoCaptchaAnswer = capturedChars.join('');
          capturedChars = [];
        }
      }
      return origFillText.apply(this, arguments);
    };
  }

  // 2. Tax Computation Engine for AY 2026-27 (FY 2025-26)
  function computeTaxAY2026_27(regime, inputs) {
    const grossSalary = Math.max(0, Number(inputs.grossSalary) || 0);
    const exemptAllowances = Math.max(0, Number(inputs.exemptAllowances) || 0);
    const profTax = Math.max(0, Number(inputs.professionalTax) || 0);
    const savingsInterest = Math.max(0, Number(inputs.savingsInterest) || 0);
    const otherIncome = Math.max(0, Number(inputs.otherIncome) || 0);
    const tdsPaid = Math.max(0, Number(inputs.tdsPaid) || 0);

    const isNew = (regime === 'new');
    const stdDeduction = isNew ? Math.min(grossSalary, 75000) : Math.min(grossSalary, 50000);

    // Income under salaries
    const salaryChargeable = Math.max(0, grossSalary - exemptAllowances - stdDeduction - profTax);
    const grossTotalIncome = salaryChargeable + savingsInterest + otherIncome;

    // Deductions under Chapter VI-A
    let d80c = 0;
    let d80d = 0;
    let d80tta = 0;
    let totalDeductions = 0;

    if (!isNew) {
      // In Old Regime: 80C max 150000, 80D max 25000, 80TTA max 10000 (limited to savings interest)
      d80c = Math.min(150000, Math.max(0, Number(inputs.deductions80c) || 0));
      d80d = Math.min(25000, Math.max(0, Number(inputs.deductions80d) || 0));
      d80tta = Math.min(10000, Math.min(savingsInterest, Math.max(0, Number(inputs.deductions80tta) || 0)));
      totalDeductions = d80c + d80d + d80tta;
    }

    const taxableIncome = Math.max(0, grossTotalIncome - totalDeductions);

    let taxBeforeCess = 0;
    let rebate87a = 0;

    if (isNew) {
      // New Regime Slabs:
      // 0 - 3,00,000 : 0%
      // 3,00,001 - 7,00,000 : 5%
      // 7,00,001 - 10,00,000 : 10%
      // 10,00,001 - 12,00,000 : 15%
      // 12,00,001 - 15,00,000 : 20%
      // Above 15,00,000 : 30%
      let rem = taxableIncome;
      if (rem > 300000) {
        const b1 = Math.min(rem - 300000, 400000);
        taxBeforeCess += b1 * 0.05;
      }
      if (rem > 700000) {
        const b2 = Math.min(rem - 700000, 300000);
        taxBeforeCess += b2 * 0.10;
      }
      if (rem > 1000000) {
        const b3 = Math.min(rem - 1000000, 200000);
        taxBeforeCess += b3 * 0.15;
      }
      if (rem > 1200000) {
        const b4 = Math.min(rem - 1200000, 300000);
        taxBeforeCess += b4 * 0.20;
      }
      if (rem > 1500000) {
        const b5 = rem - 1500000;
        taxBeforeCess += b5 * 0.30;
      }

      // Rebate u/s 87A: If income <= 7,00,000 full rebate
      if (taxableIncome <= 700000) {
        rebate87a = taxBeforeCess;
      }
    } else {
      // Old Regime Slabs:
      // 0 - 2,50,000 : 0%
      // 2,50,001 - 5,00,000 : 5% (max 12,500)
      // 5,00,001 - 10,00,000 : 20% (max 1,00,000)
      // Above 10,00,000 : 30%
      let rem = taxableIncome;
      if (rem > 250000) {
        const b1 = Math.min(rem - 250000, 250000);
        taxBeforeCess += b1 * 0.05;
      }
      if (rem > 500000) {
        const b2 = Math.min(rem - 500000, 500000);
        taxBeforeCess += b2 * 0.20;
      }
      if (rem > 1000000) {
        const b3 = rem - 1000000;
        taxBeforeCess += b3 * 0.30;
      }

      // Rebate u/s 87A: If income <= 5,00,000, max rebate 12,500
      if (taxableIncome <= 500000) {
        rebate87a = Math.min(taxBeforeCess, 12500);
      }
    }

    const taxAfterRebate = Math.max(0, taxBeforeCess - rebate87a);
    const cess = Math.round(taxAfterRebate * 0.04);
    const totalTaxLiability = taxAfterRebate + cess;

    const diff = tdsPaid - totalTaxLiability;
    const refund = diff > 0 ? diff : 0;
    const payable = diff < 0 ? -diff : 0;

    return {
      regime: isNew ? 'New Tax Regime' : 'Old Tax Regime',
      isNew,
      grossSalary,
      exemptAllowances,
      stdDeduction,
      profTax,
      salaryChargeable,
      savingsInterest,
      otherIncome,
      grossTotalIncome,
      deductions80c: d80c,
      deductions80d: d80d,
      deductions80tta: d80tta,
      totalDeductions,
      taxableIncome,
      taxBeforeCess,
      rebate87a,
      taxAfterRebate,
      cess,
      totalTaxLiability,
      tdsPaid,
      refund,
      payable,
      isRefund: diff > 0,
    };
  }

  // 3. User & Filing State Management
  const DEFAULT_USER = {
    pan: 'ABCDE1234F',
    name: 'TEST KUMAR DEMO',
    dob: '1990-01-15',
    aadhaar: '999988887777',
    mobile: '9876543210',
    email: 'test.kumar@example.com',
    flat_no: 'Flat 402, Shanti Heights',
    premises: 'Bandra West',
    street: 'Linking Road',
    locality: 'Bandra',
    city: 'Mumbai',
    state: 'Maharashtra',
    pincode: '400050',
    nature_of_employment: 'OTHERS',
    assessment_year: '2026-27',
  };

  function getUser() {
    if (window.DemoPortal && window.DemoPortal.load) {
      const u = window.DemoPortal.load('itr_user');
      if (u && u.pan) return Object.assign({}, DEFAULT_USER, u);
    }
    return Object.assign({}, DEFAULT_USER);
  }

  function saveUser(u) {
    if (window.DemoPortal && window.DemoPortal.save) {
      window.DemoPortal.save('itr_user', u);
    }
  }

  function getFiling() {
    if (window.DemoPortal && window.DemoPortal.load) {
      const f = window.DemoPortal.load('itr_filing');
      if (f) return f;
    }
    return {
      optOutRegime: 'no', // Default is new tax regime
      salary_17_1: 1250000,
      perquisites_17_2: 25000,
      profits_17_3: 0,
      gross_salary: 1275000,
      exempt_allowances: 0,
      standard_deduction: 75000,
      professional_tax: 2500,
      income_chargeable_salaries: 1197500,
      interest_savings: 10000,
      other_sources_income: 0,
      gross_total_income: 1207500,
      deduction_80c: 150000,
      deduction_80d: 25000,
      deduction_80tta: 10000,
      total_deductions: 0,
      total_taxable_income: 1207500,
      tds_entries: [
        {
          tan: 'DELD12345E',
          employer_name: 'Demo Infotech Private Limited',
          income: 1197500,
          tds: 90000,
        }
      ],
      total_tds: 90000,
      total_tax_liability: 84760,
      refund_amount: 5240,
      tax_payable: 0,
      status: 'draft',
    };
  }

  function saveFiling(f) {
    if (window.DemoPortal && window.DemoPortal.save) {
      window.DemoPortal.save('itr_filing', f);
    }
  }

  function formatRupees(num) {
    return '₹' + Number(num || 0).toLocaleString('en-IN');
  }

  window.ITREngine = {
    computeTax: computeTaxAY2026_27,
    getUser: getUser,
    saveUser: saveUser,
    getFiling: getFiling,
    saveFiling: saveFiling,
    formatRupees: formatRupees,
  };
})();
