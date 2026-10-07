import { test, expect, Page, Request, Route } from '@playwright/test'
import { createDoc, deleteDoc, uniqueSuffix } from '../helpers'

/**
 * Admin Team Activity page (TXB-287). The team_activity endpoints are mocked so every design
 * state is reachable; the suite runs as Administrator (shared auth state), and the redirect
 * checks log in as freshly created sales users.
 */

const API = '**/api/method/crm.txb.api.team_activity.'

const columns = (...labels: string[]) =>
	labels.map((label, i) => ({ key: `c${i}`, label, type: 'text' }))

const METRICS = [
	{ key: 'completed_calls', label: 'Completed calls', value: 142, record_columns: columns('When', 'By') },
	{ key: 'reached_contacts', label: 'Reached Contacts', value: 97, record_columns: [
		{ key: 'occurred_at', label: 'Latest activity', type: 'datetime' },
		{ key: 'contact', label: 'Contact', type: 'link' },
		{ key: 'activity_count', label: 'Activities', type: 'int' },
	] },
	{ key: 'agreed_meetings', label: 'Agreed meetings', value: 18, record_columns: columns('Booked', 'Status') },
]

function paramsOf(request: Request): Record<string, unknown> {
	if (request.method() === 'GET') {
		return Object.fromEntries(new URL(request.url()).searchParams)
	}
	return request.postDataJSON() || {}
}

function ok(route: Route, message: unknown) {
	return route.fulfill({ json: { message } })
}

function validationError(route: Route, message: string) {
	return route.fulfill({
		status: 417,
		json: {
			exc_type: 'ValidationError',
			_server_messages: JSON.stringify([JSON.stringify({ message })]),
		},
	})
}

/** Mock one endpoint and record the params of every call to it. */
async function mock(
	page: Page,
	method: string,
	handler: (route: Route, params: Record<string, unknown>) => unknown,
) {
	const calls: Record<string, unknown>[] = []
	await page.route(API + method + '*', (route) => {
		const params = paramsOf(route.request())
		calls.push(params)
		return handler(route, params)
	})
	return calls
}

const summaryOf = (metrics = METRICS) => ({ metrics, timezone: 'Europe/Vilnius' })

function records(total: number, page: number, rows: Record<string, unknown>[] = []) {
	return { columns: [], rows, total, page, page_length: 20 }
}

const card = (page: Page, key: string) => page.locator(`[data-metric="${key}"]`)

test.describe('Team Activity (admin)', () => {
	test('sidebar shows Team activity right after Dashboard', async ({ page }) => {
		await mock(page, 'summary', (route) => ok(route, summaryOf()))
		await page.goto('/crm/team-activity')

		const labels = await page.locator('nav a, aside a').allInnerTexts()
		const names = labels.map((l) => l.trim()).filter(Boolean)
		const dashboard = names.indexOf('Dashboard')
		expect(dashboard).toBeGreaterThanOrEqual(0)
		expect(names[dashboard + 1]).toBe('Team activity')
	})

	test('default load: last 30 days inclusive, no member, one card per metric', async ({ page }) => {
		const calls = await mock(page, 'summary', (route) => ok(route, summaryOf()))
		await page.goto('/crm/team-activity')

		await expect(card(page, 'agreed_meetings')).toContainText('18')
		expect(calls).toHaveLength(1)
		expect(calls[0]).not.toHaveProperty('member')

		const today = await page.evaluate(() =>
			new Intl.DateTimeFormat('en-CA', {
				timeZone: (window as any).timezone?.system || undefined,
			}).format(new Date()),
		)
		expect(calls[0].to_date).toBe(today)
		const days =
			(Date.parse(String(calls[0].to_date)) - Date.parse(String(calls[0].from_date))) / 86400000
		expect(days).toBe(29)

		await expect(page.locator('[data-metric]')).toHaveCount(3)
		await expect(card(page, 'completed_calls')).toContainText('Completed calls')
		await expect(card(page, 'completed_calls')).toContainText('142')
		await expect(page.getByText('Site timezone: Europe/Vilnius')).toBeVisible()
	})

	test('a 4th metric gets a 4th card and its own record columns', async ({ page }) => {
		const extra = {
			key: 'proposals_sent',
			label: 'Proposals sent',
			value: 4,
			record_columns: [
				{ key: 'sent_on', label: 'Sent on', type: 'text' },
				{ key: 'amount', label: 'Amount', type: 'int' },
			],
		}
		await mock(page, 'summary', (route) => ok(route, summaryOf([...METRICS, extra])))
		const recordCalls = await mock(page, 'records', (route) =>
			ok(route, records(1, 1, [{ sent_on: '2026-10-01', amount: 1200 }])),
		)
		await page.goto('/crm/team-activity')

		await expect(page.locator('[data-metric]')).toHaveCount(4)
		await card(page, 'proposals_sent').getByRole('button', { name: 'View records' }).click()

		const dialog = page.getByRole('dialog')
		await expect(dialog).toContainText('Proposals sent')
		await expect(dialog).toContainText('Sent on')
		await expect(dialog).toContainText('Amount')
		await expect(dialog).toContainText('1200')
		expect(recordCalls[0].metric).toBe('proposals_sent')
	})

	test('person mode: members fill the select, no-member panel, member re-calls summary', async ({ page }) => {
		const calls = await mock(page, 'summary', (route) => ok(route, summaryOf()))
		await mock(page, 'members', (route) =>
			ok(route, [
				{ name: 'ana@example.com', full_name: 'Ana Admin', user_image: null },
				{ name: 'bo@example.com', full_name: 'Bo Seller', user_image: null },
			]),
		)
		await page.goto('/crm/team-activity')
		await expect(page.locator('[data-metric]')).toHaveCount(3)

		await page.getByText('Person', { exact: true }).click()
		await expect(page.getByTestId('no-member-panel')).toBeVisible()
		await expect(page.locator('[data-metric]')).toHaveCount(0)
		expect(calls).toHaveLength(1)

		await page.getByText('Team member', { exact: true }).click()
		await page.getByRole('option', { name: 'Bo Seller' }).click()
		await expect(page.locator('[data-metric]')).toHaveCount(3)
		expect(calls.at(-1)?.member).toBe('bo@example.com')

		await page.getByText('Whole team', { exact: true }).click()
		await expect.poll(() => calls.length).toBe(3)
		expect(calls.at(-1)).not.toHaveProperty('member')
	})

	test('Back to whole team leaves the empty person panel and refetches without a member', async ({ page }) => {
		const calls = await mock(page, 'summary', (route) => ok(route, summaryOf()))
		await mock(page, 'members', (route) => ok(route, []))
		await page.goto('/crm/team-activity')
		await expect(page.locator('[data-metric]')).toHaveCount(3)

		await page.getByText('Person', { exact: true }).click()
		await page.getByRole('button', { name: 'Back to whole team' }).click()
		await expect(page.locator('[data-metric]')).toHaveCount(3)
		expect(calls.at(-1)).not.toHaveProperty('member')
	})

	test('loading skeletons, error panel with Retry, and the zero state', async ({ page }) => {
		let release: () => void = () => {}
		const pending = new Promise<void>((resolve) => (release = resolve))
		let attempt = 0
		await mock(page, 'summary', async (route) => {
			attempt += 1
			if (attempt === 1) {
				await pending
				return route.fulfill({ status: 500, json: { exc_type: 'Exception' } })
			}
			return ok(route, summaryOf(METRICS.map((m) => ({ ...m, value: 0 }))))
		})
		await page.goto('/crm/team-activity')

		await expect(page.locator('.fui-skeleton').first()).toBeVisible()
		release()

		const errorPanel = page.getByTestId('error-panel')
		await expect(errorPanel).toContainText("Couldn't load team activity")
		await errorPanel.getByRole('button', { name: 'Retry' }).click()

		await expect(page.locator('[data-metric]')).toHaveCount(3)
		for (const metric of METRICS) await expect(card(page, metric.key)).toContainText('0')
		await expect(page.getByText('No activity in this period')).toBeVisible()
		expect(attempt).toBe(2)
	})

	test('records dialog: same filters, API total, Next/Previous pages', async ({ page }) => {
		const summaryCalls = await mock(page, 'summary', (route) => ok(route, summaryOf()))
		const recordCalls = await mock(page, 'records', (route, params) => {
			const pageNo = Number(params.page)
			const rows = Array.from({ length: pageNo === 1 ? 20 : 5 }, (_, i) => ({
				c0: `row ${(pageNo - 1) * 20 + i + 1}`,
				c1: 'x',
			}))
			return ok(route, records(25, pageNo, rows))
		})
		await page.goto('/crm/team-activity')
		await card(page, 'completed_calls').getByRole('button', { name: 'View records' }).click()

		const dialog = page.getByRole('dialog')
		await expect(dialog).toContainText('25 records')
		await expect(dialog).toContainText('1–20 of 25')
		expect(recordCalls[0]).toMatchObject({
			metric: 'completed_calls',
			from_date: summaryCalls[0].from_date,
			to_date: summaryCalls[0].to_date,
		})
		expect(Number(recordCalls[0].page)).toBe(1)
		expect(recordCalls[0]).not.toHaveProperty('member')

		await dialog.getByRole('button', { name: 'Next' }).click()
		await expect(dialog).toContainText('21–25 of 25')
		expect(Number(recordCalls.at(-1)?.page)).toBe(2)
		await expect(dialog.getByRole('button', { name: 'Next' })).toBeDisabled()

		await dialog.getByRole('button', { name: 'Previous' }).click()
		await expect(dialog).toContainText('1–20 of 25')
		expect(Number(recordCalls.at(-1)?.page)).toBe(1)
	})

	test('reach badge only above 1, empty records, and records error with Retry', async ({ page }) => {
		await mock(page, 'summary', (route) => ok(route, summaryOf()))
		let mode: 'rows' | 'empty' | 'error' = 'rows'
		await mock(page, 'records', (route) => {
			if (mode === 'empty') return ok(route, records(0, 1))
			if (mode === 'error') return route.fulfill({ status: 500, json: { exc_type: 'Exception' } })
			return ok(
				route,
				records(2, 1, [
					{ occurred_at: '2026-10-01 10:00:00', contact: 'Jonas', activity_count: 3 },
					{ occurred_at: '2026-10-02 10:00:00', contact: 'Rasa', activity_count: 1 },
				]),
			)
		})
		await page.goto('/crm/team-activity')

		const openReach = () =>
			card(page, 'reached_contacts').getByRole('button', { name: 'View records' }).click()
		await openReach()
		const dialog = page.getByRole('dialog')
		await expect(dialog).toContainText('3 activities')
		await expect(dialog).not.toContainText('1 activities')
		await page.keyboard.press('Escape')

		mode = 'empty'
		await openReach()
		await expect(dialog).toContainText('No records in this period')
		await page.keyboard.press('Escape')

		mode = 'error'
		await openReach()
		await expect(dialog).toContainText("Couldn't load the records")
		mode = 'rows'
		await dialog.getByRole('button', { name: 'Retry' }).click()
		await expect(dialog).toContainText('3 activities')
	})

	test('a reversed custom range is blocked with no API call; server 4xx shows in the error panel', async ({ page }) => {
		let reject = false
		const calls = await mock(page, 'summary', (route) =>
			reject
				? validationError(route, 'Bad period from the server')
				: ok(route, summaryOf()),
		)
		await page.goto('/crm/team-activity')
		await expect(page.locator('[data-metric]')).toHaveCount(3)

		await page.getByRole('button', { name: 'Last 30 Days' }).click()
		await page.getByText('Custom Range', { exact: true }).click()
		const before = calls.length

		const from = page.getByPlaceholder('From')
		await from.fill('2026-10-05')
		await from.press('Enter')
		const to = page.getByPlaceholder('To')
		await to.fill('2026-10-01')
		await to.press('Enter')

		await expect(page.getByText('The start date must not be after the end date.')).toBeVisible()
		await expect(page.locator('[data-metric]')).toHaveCount(0)
		expect(calls.slice(before).every((p) => String(p.from_date) <= String(p.to_date))).toBe(true)

		reject = true
		await to.fill('2026-10-09')
		await to.press('Enter')
		await expect(page.getByTestId('error-panel')).toContainText('Bad period from the server')
	})
})

test.describe('Team Activity (sales roles)', () => {
	for (const role of ['Sales User', 'Sales Manager']) {
		test(`${role} has no link and is redirected without any team_activity call`, async ({ browser, request }) => {
			const email = `e2e-ta-${uniqueSuffix()}@example.com`
			const password = `E2e!${uniqueSuffix()}Pw#9`
			await createDoc(request, 'User', {
				email,
				first_name: `E2E ${role}`,
				send_welcome_email: 0,
				new_password: password,
				roles: [{ role }],
			})

			const context = await browser.newContext({ storageState: { cookies: [], origins: [] } })
			try {
				const login = await context.request.post('/api/method/login', {
					form: { usr: email, pwd: password },
				})
				expect(login.ok()).toBe(true)

				const page = await context.newPage()
				const apiCalls: string[] = []
				page.on('request', (r) => {
					if (r.url().includes('crm.txb.api.team_activity')) apiCalls.push(r.url())
				})

				await page.goto('/crm/leads')
				await expect(page.getByRole('link', { name: 'Leads' }).first()).toBeVisible()
				await expect(page.getByRole('link', { name: 'Team activity' })).toHaveCount(0)

				await page.goto('/crm/team-activity')
				await expect(page).toHaveURL(/\/crm\/not-permitted/)
				expect(apiCalls).toEqual([])
			} finally {
				await context.close()
				await deleteDoc(request, 'User', email).catch(() => {})
			}
		})
	}
})
