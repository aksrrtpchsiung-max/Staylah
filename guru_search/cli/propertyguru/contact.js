import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';

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
  name: 'contact',
  access: 'read',
  description: 'Extract agent contact and generate WhatsApp link for a listing.',
  domain: 'www.propertyguru.com.sg',
  strategy: Strategy.COOKIE,
  browser: true,
  args: [
    { name: 'id', type: 'string', positional: true, required: true, help: 'Listing ID (numeric) or full URL' },
  ],
  columns: ['listingId', 'listingTitle', 'agentName', 'agentMobile', 'ceaLicense', 'whatsappUrl', 'listingUrl'],
  func: async (page, kwargs) => {
    const url = resolveListingUrl(kwargs.id);
    await page.goto(url, { settleMs: 2000 });
    await page.wait(2);

    const data = await page.evaluate(() => {
      try {
        const d = window.__NEXT_DATA__?.props?.pageProps?.pageData?.data;
        if (!d) return { error: 'page did not load' };

        const card = d.contactAgentData?.contactAgentCard;
        const agentProps = card?.agentInfoProps?.agent;
        const allActions = [];
        for (const act of card?.richContactActions || []) {
          if (act.groupLayout?.actions) allActions.push(...act.groupLayout.actions);
          else allActions.push(act);
        }

        const hasWhatsapp = allActions.some(a => a.type === 'whatsapp');
        const mobile = hasWhatsapp ? (agentProps?.mobile || '') : '';

        // Extract monthly price from DOM (the detailItems PSF price is not the monthly rent)
        const priceEl = document.querySelector('.amount, [class*="listing-price"] .amount');
        const priceText = priceEl?.textContent?.trim() || '';

        // Build WhatsApp message
        const agentName = agentProps?.name || '';
        const projectName = d.descriptionBlockData?.subtitle || '';
        const listingUrl = window.location.href;

        const msg = [
          `Hi ${agentName},`,
          '',
          `I'm interested in ${projectName}${priceText ? ` (${priceText})` : ''}.`,
          'Is it still available? When can I view?',
          '',
          listingUrl,
        ].join('\n');

        const whatsappUrl = mobile ? `https://wa.me/${mobile.replace(/^\+/, '')}?text=${encodeURIComponent(msg)}` : '';

        return {
          agentName,
          agentMobile: mobile,
          ceaLicense: (agentProps?.description || '').replace(/<[^>]+>/g, '').trim(),
          whatsappUrl,
          listingTitle: projectName,
        };
      } catch (err) {
        return { error: err?.message || 'unknown error' };
      }
    });

    if (data?.error) throw new CommandExecutionError(data.error);

    return [{
      listingId: kwargs.id,
      listingTitle: data.listingTitle || '(see listing)',
      agentName: data.agentName,
      agentMobile: data.agentMobile,
      ceaLicense: data.ceaLicense,
      whatsappUrl: data.whatsappUrl,
      listingUrl: url,
    }];
  },
});
