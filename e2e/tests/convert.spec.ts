import { test, expect } from '@playwright/test'
import { LeadPage } from '../pages'
import { cleanupE2ERecords, DEAL_DOCTYPE, getList, seedLead } from '../helpers'

test.describe('Lead to deal conversion', () => {
	test.afterAll(async ({ request }) => {
		await cleanupE2ERecords(request)
	})

	test('converts an existing lead into a deal', async ({ page, request }) => {
		const lead = await seedLead(request)
		const leadPage = new LeadPage(page)

		await leadPage.goto(lead.name)
		await leadPage.convertToDeal()

		// A deal now carries the converted lead's email.
		await expect
			.poll(async () => {
				const rows = await getList(request, DEAL_DOCTYPE, {
					filters: { email: lead.email },
					fields: ['name'],
				})
				return rows.length
			})
			.toBeGreaterThan(0)
	})

	test('shows the lead organization without a contact section', async ({
		page,
		request,
	}) => {
		const lead = await seedLead(request)
		const leadPage = new LeadPage(page)

		await leadPage.goto(lead.name)
		const dialog = await leadPage.openConvertToDealModal()

		// The lead already has an organization, so it is shown read-only rather than
		// asking the user to pick it again.
		await expect(dialog.getByText(lead.organization!)).toBeVisible()
		await expect(dialog.getByRole('button', { name: 'Change' })).toBeVisible()

		// The contact section is gone; the contact is derived from the lead.
		await expect(dialog.getByText('Choose Existing')).toHaveCount(0)
	})

	test('blocks conversion when the lead has no organization', async ({
		page,
		request,
	}) => {
		const lead = await seedLead(request, { organization: '' })
		const leadPage = new LeadPage(page)

		await leadPage.goto(lead.name)
		const dialog = await leadPage.openConvertToDealModal()

		// The picker explains why an organization is needed.
		await expect(
			dialog.getByText('Every opportunity needs an organization', {
				exact: false,
			}),
		).toBeVisible()

		await dialog.getByRole('button', { name: 'Convert', exact: true }).click()

		// Without an organization the deal would render untitled, so conversion is
		// refused and the modal stays open.
		await expect(
			dialog.getByText('Please select or create an organization'),
		).toBeVisible()
		await expect(
			dialog.getByRole('heading', { name: 'Convert to Deal' }),
		).toBeVisible()
	})
})
