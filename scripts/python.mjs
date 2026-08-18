import { existsSync } from 'node:fs'
import { spawn, spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const isWindows = process.platform === 'win32'
const venvPython = join(
  projectRoot,
  '.venv',
  isWindows ? 'Scripts' : 'bin',
  isWindows ? 'python.exe' : 'python',
)

function systemCandidates() {
  const configured = process.env.NOVAFRAME_PYTHON?.trim()
  const candidates = []
  if (configured) candidates.push({ command: configured, prefix: [] })
  if (isWindows) candidates.push({ command: 'py', prefix: ['-3'] })
  candidates.push({ command: isWindows ? 'python' : 'python3', prefix: [] })
  if (!isWindows) candidates.push({ command: 'python', prefix: [] })
  return candidates
}

function isRunnable(candidate) {
  const result = spawnSync(candidate.command, [...candidate.prefix, '--version'], {
    cwd: projectRoot,
    stdio: 'ignore',
    windowsHide: true,
  })
  return result.status === 0
}

function findSystemPython() {
  const candidate = systemCandidates().find(isRunnable)
  if (!candidate) {
    throw new Error('未找到 Python 3。请先安装 Python 3.11 或更高版本。')
  }
  return candidate
}

function runSync(candidate, args, label) {
  const result = spawnSync(candidate.command, [...candidate.prefix, ...args], {
    cwd: projectRoot,
    stdio: 'inherit',
    windowsHide: true,
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`${label}失败，退出码 ${result.status ?? 'unknown'}`)
  }
}

function bootstrap() {
  if (!existsSync(venvPython)) {
    runSync(findSystemPython(), ['-m', 'venv', '.venv'], '创建 Python 虚拟环境')
  }
  const runtime = { command: venvPython, prefix: [] }
  runSync(runtime, ['-m', 'pip', 'install', '--upgrade', 'pip'], '升级 pip')
  runSync(runtime, ['-m', 'pip', 'install', '-r', 'backend/requirements.txt'], '安装后端依赖')
}

const args = process.argv.slice(2)

try {
  if (args[0] === '--bootstrap') {
    bootstrap()
    process.exit(0)
  }

  const runtime = existsSync(venvPython)
    ? { command: venvPython, prefix: [] }
    : findSystemPython()
  const child = spawn(runtime.command, [...runtime.prefix, ...args], {
    cwd: projectRoot,
    stdio: 'inherit',
    windowsHide: true,
  })

  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.once(signal, () => {
      if (!child.killed) child.kill(signal)
    })
  }

  child.once('error', (error) => {
    console.error(`无法启动 Python：${error.message}`)
    process.exit(1)
  })
  child.once('exit', (code, signal) => {
    process.exit(code ?? (signal ? 1 : 0))
  })
} catch (error) {
  console.error(error instanceof Error ? error.message : error)
  process.exit(1)
}
