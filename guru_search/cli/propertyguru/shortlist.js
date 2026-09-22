import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';

const BASE_URL = 'https://www.propertyguru.com.sg';

function resolveListingUrl(input) {
  const raw = String(input ?? '').trim();
  if (!raw) throw new ArgumentError('listing id or url is required');
  if (raw.startsWith('https://www.propertyguru.com.sg/listing/')) return raw;
  if (/^\d+$/.test(raw)) return `${BASE_URL}/listing/${raw}`;
  throw new ArgumentError(`Invalid listing id or url: "${raw}". Pass a numeric ID or full propertyguru.sg listing URL.`);
}

cli({
  site: 'propertyguru',
  name: 'shortlist',
  access: 'write',
  description: 'Shortlist (bookmark) a PropertyGuru listing. Requires login. Toggles: click again to un-shortlist.',
  domain: 'www.propertyguru.com.sg',
  strategy: Strategy.UI,
  browser: true,
  args: [
    { name: 'id', type: 'string', positional: true, required: true, help: 'Listing ID (numeric) or full URL from search results' },
  ],
  columns: ['status', 'listingId', 'action', 'detail'],
  func: async (page, kwargs) => {
    const url = resolveListingUrl(kwargs.id);
    await page.goto(url, { settleMs: 2000 });
    await page.wait(2);

    // Check login state first
    const loginCheck = await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Shortlist' || b.textContent.trim() === 'Shortlisted');
      return btn ? { found: true, text: btn.textContent.trim() } : { found: false };
    });

    if (!loginCheck.found) {
      // Maybe not logged in — the shortlist button won't appear
      const pageText = await page.evaluate(() => document.body?.innerText?.slice(0, 500) || '');
      if (/login|sign in|log in/i.test(pageText)) {
        throw new AuthRequiredError('propertyguru.com.sg', 'Login required to shortlist listings.');
      }
      throw new CommandExecutionError('Shortlist button not found on page. You may need to log in first.');
    }

    const beforeState = loginCheck.text;

    // If already shortlisted, we're done
    if (beforeState === 'Shortlisted') {
      return [{
        status: 'already_shortlisted',
        listingId: kwargs.id,
        action: 'none',
        detail: 'This listing was already in your shortlist.',
      }];
    }

    // Click the shortlist button
    const clickResult = await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Shortlist');
      if (!btn) return { ok: false, reason: 'button disappeared' };
      btn.click();
      return { ok: true };
    });

    if (!clickResult.ok) {
      throw new CommandExecutionError(`Failed to click shortlist button: ${clickResult.reason}`);
    }

    await page.wait(1.5);

    // Verify state change
    const afterCheck = await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Shortlisted');
      return btn ? { shortlisted: true } : { shortlisted: false };
    });

    return [{
      status: afterCheck.shortlisted ? 'shortlisted' : 'unknown',
      listingId: kwargs.id,
      action: 'add',
      detail: afterCheck.shortlisted ? 'Successfully added to shortlist.' : 'Button clicked but state unconfirmed.',
    }];
  },
});
