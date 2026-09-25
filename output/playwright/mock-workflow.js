async (page) => {
  await page.route('**/api/tasks/plan', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: 'mock-task',
        status: 'awaiting_approval',
        plan: [
          { id: 1, title: 'Inspect implementation', description: 'Read the failing function and tests.', status: 'pending' },
          { id: 2, title: 'Apply and verify fix', description: 'Update the implementation and run pytest.', status: 'pending' },
        ],
      }),
    })
  })

  await page.route('**/api/tasks/mock-task/execute', async (route) => {
    const events = [
      { type: 'start', agent: 'orchestrator', iteration: 0, message: 'Execution started', data: {} },
      { type: 'hand_off', agent: 'coder', iteration: 1, message: 'Coder received the approved plan', data: {} },
      { type: 'tool_result', agent: 'tester', iteration: 1, message: '1 test passed', data: { tool: 'run_tests' } },
      { type: 'final', agent: 'reviewer', iteration: 1, message: 'Review approved', data: {} },
    ]
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
    })
  })

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('http://127.0.0.1:5173/')
  await page.getByRole('button', { name: 'Generate Plan' }).click()
  await page.getByText('Inspect implementation').waitFor()
  await page.getByRole('button', { name: 'Approve & Execute' }).click()
  await page.getByText('Review approved').waitFor()
  await page.getByText('completed', { exact: true }).waitFor()
  return {
    planSteps: await page.locator('.plan-step').count(),
    traceEvents: await page.locator('.trace-event').count(),
    status: await page.locator('.task-status').innerText(),
  }
}
