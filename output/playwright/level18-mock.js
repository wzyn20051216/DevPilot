async (page) => {
  await page.route('**/api/tasks/plan', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        task_id: 'mock-task',
        status: 'awaiting_approval',
        plan: [
          { id: 1, title: 'Inspect implementation', description: 'Read the failing function and its tests.', status: 'pending' },
          { id: 2, title: 'Apply focused fix', description: 'Correct the calculation without changing public APIs.', status: 'pending' },
          { id: 3, title: 'Verify behavior', description: 'Run pytest and review the final diff.', status: 'pending' },
        ],
      }),
    })
  })

  await page.route('**/api/tasks/mock-task/diff', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        diff: 'diff --git a/calculator.py b/calculator.py\n--- a/calculator.py\n+++ b/calculator.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b',
      }),
    })
  })

  await page.route('**/api/tasks/mock-task/execute', async (route) => {
    const events = [
      { type: 'start', agent: 'orchestrator', iteration: 0, message: 'Execution started', data: {} },
      { type: 'tool_result', agent: 'coder', iteration: 2, message: 'calculator.py updated', data: { tool: 'write_file', arguments: { file_path: 'calculator.py' } } },
      { type: 'tool_result', agent: 'tester', iteration: 1, message: '1 test passed', data: { tool: 'run_test', arguments: {} } },
      {
        type: 'final',
        agent: 'orchestrator',
        iteration: 0,
        message: 'DevPilot completed the task.',
        data: {
          test_report: { passed: true, summary: '1 passed in 0.08s', stdout: 'tests/test_calculator.py . [100%]', stderr: '' },
          review_report: { approved: true, summary: 'The implementation now matches the expected behavior.', issues: [] },
        },
      },
    ]
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
    })
  })

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('http://127.0.0.1:5173/')
  await page.getByRole('button', { name: 'Generate Plan' }).click()
  await page.getByRole('heading', { name: 'Review execution plan' }).waitFor()
  await page.getByRole('button', { name: 'Approve & Execute' }).click()
  await page.getByText('DevPilot completed the task.').waitFor()
  await page.getByText('completed', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Tests' }).click()
  await page.getByText('Tests passed').waitFor()
  return {
    planSteps: await page.locator('.plan-step').count(),
    traceEvents: await page.locator('.trace-event').count(),
    testReport: await page.getByText('Tests passed').isVisible(),
  }
}
