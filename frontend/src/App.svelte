<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let rows = []
  let herb = '白芍'
  let tempC = 110
  let minutes = 10
  let error = ''

  let quota = null
  let ledger = []
  let quotaInput = 100
  let energyError = ''
  let energyMsg = ''

  function viewFromHash() {
    return location.hash === '#energy' ? 'energy' : 'records'
  }
  let view = viewFromHash()

  window.addEventListener('hashchange', () => {
    view = viewFromHash()
    if (view === 'energy' && token) loadEnergy()
  })

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    token = data.access_token
    role = data.role
    localStorage.setItem('herb_token', token)
    localStorage.setItem('herb_role', role)
    await load()
  }

  async function load() {
    rows = await api('/api/batches')
    await loadEnergy()
  }

  async function loadEnergy() {
    quota = await api('/api/energy/quota')
    ledger = await api('/api/energy/ledger')
  }

  async function save() {
    error = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      await load()
    } catch (err) {
      error = err.message
    }
  }

  async function saveQuota() {
    energyError = ''
    energyMsg = ''
    try {
      await api('/api/energy/quota', {
        method: 'PUT',
        body: JSON.stringify({ points: Number(quotaInput) }),
      })
      energyMsg = `额度已设为 ${Number(quotaInput)} 点，下一次提交起生效`
      await loadEnergy()
    } catch (err) {
      energyError = err.message
    }
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
  }

  function fmtTime(iso) {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  }

  const kindName = { set: '设额度', consume: '开炒消耗', reset: '零点清零' }

  if (token) load()
</script>

<main>
  <h1>饮片炮制记录台</h1>
  {#if !token}
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。开炒按能耗日额度卡控。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <nav class="topbar">
      <a href="#records" class:active={view === 'records'}>炮制记录</a>
      <a href="#energy" class:active={view === 'energy'}>能耗额度</a>
      <span class="spacer"></span>
      <span class="who">{username}（{role === 'writer' ? '炮制员' : '质检员'}）</span>
      <button on:click={leave}>退出</button>
    </nav>

    {#if view === 'records'}
      {#if quota}
        <p class="hint">今日能耗：已用 {quota.used} 点 / 额度 {quota.quota} 点（剩余 {quota.remaining} 点）</p>
      {/if}
      {#if role === 'writer'}
        <input bind:value={herb} placeholder="饮片" />
        <input type="number" bind:value={tempC} />
        <input type="number" bind:value={minutes} />
        <button on:click={save}>写入清炒记录</button>
        {#if error}<p class="err">{error}</p>{/if}
      {/if}
      <ul>
        {#each rows as row}
          <li>{row.herb} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c}</li>
        {/each}
      </ul>
    {:else}
      <h2>能耗额度</h2>
      {#if quota}
        <section class="panel">
          <h3>今日已用</h3>
          <p class="big">{quota.used} / {quota.quota} 点 <span class="dim">（剩余 {quota.remaining} 点，{quota.day}）</span></p>
        </section>
      {/if}
      {#if role === 'writer'}
        <section class="panel">
          <h3>额度设置</h3>
          <input type="number" min="0" bind:value={quotaInput} />
          <button on:click={saveQuota}>设定本日额度</button>
          {#if energyMsg}<p class="ok">{energyMsg}</p>{/if}
          {#if energyError}<p class="err">{energyError}</p>{/if}
        </section>
      {:else}
        <p class="dim">质检员仅可查阅已用点数与额度流水，不可改额度、不可开炒。</p>
      {/if}
      <section class="panel">
        <h3>额度流水</h3>
        <table>
          <thead>
            <tr><th>时间</th><th>类型</th><th>点数</th><th>已用/额度</th><th>操作人</th><th>说明</th></tr>
          </thead>
          <tbody>
            {#each ledger as entry}
              <tr>
                <td>{fmtTime(entry.created_at)}</td>
                <td>{kindName[entry.kind] || entry.kind}</td>
                <td>{entry.points}</td>
                <td>{entry.used_after} / {entry.quota_after}</td>
                <td>{entry.actor}</td>
                <td>{entry.note}</td>
              </tr>
            {/each}
            {#if ledger.length === 0}
              <tr><td colspan="6" class="dim">暂无流水</td></tr>
            {/if}
          </tbody>
        </table>
      </section>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 720px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  input { margin-right: 8px; padding: 6px; }
  .topbar { display: flex; align-items: center; gap: 8px; border-bottom: 2px solid #e7d8c9; padding-bottom: 10px; margin-bottom: 14px; }
  .topbar .spacer { flex: 1; }
  .topbar .who { color: #8a6d4b; font-size: 14px; }
  .topbar a { padding: 6px 12px; border: 1px solid #d6c3ae; border-radius: 4px; color: #3f2f1f; text-decoration: none; }
  .topbar a.active { background: #7c2d12; color: #fff; border-color: #7c2d12; }
  .topbar button { padding: 6px 12px; border: 1px solid #d6c3ae; background: #fff; cursor: pointer; border-radius: 4px; }
  .hint { background: #fdf3e7; border: 1px solid #edd9bd; padding: 8px 10px; border-radius: 4px; }
  .panel { border: 1px solid #e7d8c9; border-radius: 6px; padding: 12px 14px; margin-bottom: 14px; }
  .panel h3 { margin-top: 0; color: #7c2d12; }
  .big { font-size: 22px; margin: 4px 0; }
  .dim { color: #8a6d4b; font-size: 14px; }
  .err { color: #b91c1c; }
  .ok { color: #15803d; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { border-bottom: 1px solid #eee0cf; padding: 6px 8px; text-align: left; }
  th { color: #7c2d12; }
</style>
