import { defineConfig } from 'vite'
import FullReload from 'vite-plugin-full-reload'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'
import { spawn } from 'child_process'

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const repoRoot = path.resolve(projectRoot, '..')
// '/pipeline/' serves data files (metadata CSVs) that live at the repo root.
// A previous version pointed this at <repo>/pipeline, which does not exist.
const pipelineDir = repoRoot
const getAndChunkDir = path.resolve(projectRoot, '..', 'Get_and_Chunk')
const vectorDBDir = path.resolve(projectRoot, '..', 'VectorDB')
const appPathsPath = path.resolve(projectRoot, 'config', 'paths.json')
const appPaths = JSON.parse(fs.readFileSync(appPathsPath, 'utf8'))
const backupsRoot = path.resolve(projectRoot, 'config', 'backups')
const activeScriptProcesses = new Map()
// Retains the last run's state per script for /api/run-status polling.
const scriptRunStates = new Map()
const MAX_RUN_OUTPUT_CHARS = 512 * 1024

function isInsideDir(baseDir, targetPath) {
  const rel = path.relative(baseDir, targetPath)
  return rel !== '' && !rel.startsWith('..') && !path.isAbsolute(rel)
}

const staticBuildEntries = [
  'home.html',
  'source.html',
  'crawl.html',
  'crawl_pdf.html',
  'clean.html',
  'chunk.html',
  'summary.html',
  'training_data.html',
  'embed.html',
  'vector.html',
  'style.css',
  'js',
  'css',
  'images',
  'config',
]

function copyPathIfExists(fromPath, toPath) {
  if (!fs.existsSync(fromPath)) {
    return
  }

  const stats = fs.statSync(fromPath)
  if (stats.isDirectory()) {
    fs.mkdirSync(toPath, { recursive: true })
    const children = fs.readdirSync(fromPath)
    for (const child of children) {
      copyPathIfExists(path.join(fromPath, child), path.join(toPath, child))
    }
    return
  }

  fs.mkdirSync(path.dirname(toPath), { recursive: true })
  fs.copyFileSync(fromPath, toPath)
}

function copyStaticBuildResources() {
  return {
    name: 'copy-static-build-resources',
    apply: 'build',
    closeBundle() {
      const outDir = path.resolve(projectRoot, 'build')
      for (const entry of staticBuildEntries) {
        const sourcePath = path.resolve(projectRoot, entry)
        const targetPath = path.resolve(outDir, entry)
        copyPathIfExists(sourcePath, targetPath)
      }
    },
  }
}

function buildBackupPath(targetPath) {
  const safeTimestamp = new Date().toISOString().replace(/[:.]/g, '-')
  const relPath = path.relative(repoRoot, targetPath)
  return path.resolve(backupsRoot, `${relPath}.${safeTimestamp}.bak`)
}

function createBackupSnapshot(targetPath, callback) {
  fs.stat(targetPath, (statErr, statInfo) => {
    if (statErr) {
      if (statErr.code === 'ENOENT') {
        callback(null, null)
        return
      }
      callback(statErr)
      return
    }

    if (!statInfo.isFile()) {
      callback(new Error(`Backup source is not a file: ${targetPath}`))
      return
    }

    const backupPath = buildBackupPath(targetPath)
    fs.mkdir(path.dirname(backupPath), { recursive: true }, mkdirErr => {
      if (mkdirErr) {
        callback(mkdirErr)
        return
      }

      fs.copyFile(targetPath, backupPath, copyErr => {
        if (copyErr) {
          callback(copyErr)
          return
        }
        callback(null, backupPath)
      })
    })
  })
}

/**
 * Generic file proxy factory for serving files from a mapped directory.
 * @param {string} urlPrefix - URL path prefix (e.g. '/pipeline/' or '/Get_and_Chunk/').
 * @param {string} baseDir - Absolute directory to serve from.
 * @param {string} pluginName - Vite plugin name.
 * @param {string[]} [allowedExts] - Servable file extensions (default: .py/.json/.csv).
 */
function createFileProxy(urlPrefix, baseDir, pluginName, allowedExts = ['.py', '.json', '.csv']) {
  return {
    name: pluginName,
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        // Only handle GET/HEAD — let PUT/POST fall through to write proxy
        if (!req.url || !req.url.startsWith(urlPrefix) || (req.method !== 'GET' && req.method !== 'HEAD')) {
          next()
          return
        }

        const regex = new RegExp(`^${urlPrefix.replace(/\//g, '\\/')}`)
        const relativePath = req.url.replace(regex, '').split('?')[0].split('#')[0]
        if (!relativePath) {
          res.statusCode = 404
          res.end(`Missing path segment for ${urlPrefix}.`)
          return
        }

        const normalized = path.normalize(path.join(baseDir, relativePath))

        if (!isInsideDir(baseDir, normalized)) {
          res.statusCode = 403
          res.end('Access to the requested resource is forbidden.')
          return
        }

        if (!allowedExts.includes(path.extname(normalized).toLowerCase())) {
          res.statusCode = 403
          res.end('File type not served by this proxy.')
          return
        }

        fs.readFile(normalized, (err, data) => {
          if (err) {
            res.statusCode = err.code === 'ENOENT' ? 404 : 500
            res.end(`Unable to read ${relativePath}: ${err.message}`)
            return
          }

          const ext = path.extname(normalized).toLowerCase()
          const mimeMap = {
            '.csv': 'text/csv; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.py': 'text/plain; charset=utf-8',
          }
          res.setHeader('Content-Type', mimeMap[ext] || 'text/plain; charset=utf-8')
          res.end(data)
        })
      })
    },
  }
}

/**
 * Write-capable proxy: accepts PUT requests to save file content back to disk.
 *
 * Writes are restricted to an explicit allowlist of relative-path patterns —
 * a previous version accepted any .py under the mapped directory, which meant
 * the browser could overwrite the pipeline scripts themselves (and then run
 * them via /api/run-script).
 *
 * @param {RegExp[]} allowedPatterns - Tested against the forward-slash relative path.
 */
function createWriteProxy(urlPrefix, baseDir, pluginName, allowedPatterns) {
  return {
    name: pluginName,
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url || !req.url.startsWith(urlPrefix) || req.method !== 'PUT') {
          next()
          return
        }

        const regex = new RegExp(`^${urlPrefix.replace(/\//g, '\\/')}`)
        const relativePath = req.url.replace(regex, '').split('?')[0].split('#')[0]
        if (!relativePath) {
          res.statusCode = 400
          res.end(JSON.stringify({ success: false, error: 'Missing path segment.' }))
          return
        }

        const normalized = path.normalize(path.join(baseDir, relativePath))

        if (!isInsideDir(baseDir, normalized)) {
          res.statusCode = 403
          res.end(JSON.stringify({ success: false, error: 'Forbidden.' }))
          return
        }

        const relForwardSlash = path.relative(baseDir, normalized).replace(/\\/g, '/')
        if (!allowedPatterns.some(p => p.test(relForwardSlash))) {
          res.statusCode = 403
          res.end(JSON.stringify({ success: false, error: `Writing ${relForwardSlash} is not allowed.` }))
          return
        }

        let body = ''
        req.on('data', chunk => { body += chunk })
        req.on('end', () => {
          createBackupSnapshot(normalized, (backupErr, backupPath) => {
            res.setHeader('Content-Type', 'application/json; charset=utf-8')
            if (backupErr) {
              res.statusCode = 500
              res.end(JSON.stringify({ success: false, error: `Backup failed: ${backupErr.message}` }))
              return
            }

            fs.writeFile(normalized, body, 'utf8', (err) => {
              if (err) {
                res.statusCode = 500
                res.end(JSON.stringify({ success: false, error: err.message }))
              } else {
                res.statusCode = 200
                res.end(JSON.stringify({ success: true, backupPath }))
              }
            })
          })
        })
      })
    },
  }
}

function pipelineFileProxy() {
  // Repo-root data files (metadata CSVs) only.
  return createFileProxy('/pipeline/', pipelineDir, 'pipeline-file-proxy', ['.csv'])
}

function pipelineWriteProxy() {
  // Lets the Source tab save selected rows next to the metadata CSV.
  return createWriteProxy('/pipeline/', pipelineDir, 'pipeline-write-proxy', [
    /^[\w.-]+\.csv$/,
  ])
}

function getAndChunkFileProxy() {
  return createFileProxy('/Get_and_Chunk/', getAndChunkDir, 'get-and-chunk-file-proxy')
}

function getAndChunkWriteProxy() {
  return createWriteProxy('/Get_and_Chunk/', getAndChunkDir, 'get-and-chunk-write-proxy', [
    /^config\/[\w.-]+\.(py|json)$/,
  ])
}

function vectorDBFileProxy() {
  return createFileProxy('/VectorDB/', vectorDBDir, 'vectordb-file-proxy')
}

function vectorDBWriteProxy() {
  return createWriteProxy('/VectorDB/', vectorDBDir, 'vectordb-write-proxy', [
    /^config\/[\w.-]+\.(py|json)$/,
  ])
}

/**
 * Run-settings API: GET/PUT /api/run-settings
 * Provides read/write access to repo-root run_settings.py during dev.
 */
function createRunSettingsApi() {
  const runSettingsPath = path.resolve(repoRoot, 'run_settings.py')

  return {
    name: 'run-settings-api',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url !== '/api/run-settings') {
          next()
          return
        }

        if (req.method === 'GET' || req.method === 'HEAD') {
          fs.readFile(runSettingsPath, (err, data) => {
            if (err) {
              res.statusCode = err.code === 'ENOENT' ? 404 : 500
              res.end(`Unable to read run_settings.py: ${err.message}`)
              return
            }
            res.setHeader('Content-Type', 'text/plain; charset=utf-8')
            res.end(data)
          })
          return
        }

        if (req.method === 'PUT') {
          let body = ''
          req.on('data', chunk => { body += chunk })
          req.on('end', () => {
            createBackupSnapshot(runSettingsPath, (backupErr, backupPath) => {
              res.setHeader('Content-Type', 'application/json; charset=utf-8')
              if (backupErr) {
                res.statusCode = 500
                res.end(JSON.stringify({ success: false, error: `Backup failed: ${backupErr.message}` }))
                return
              }

              fs.writeFile(runSettingsPath, body, 'utf8', err => {
                if (err) {
                  res.statusCode = 500
                  res.end(JSON.stringify({ success: false, error: err.message }))
                } else {
                  res.statusCode = 200
                  res.end(JSON.stringify({ success: true, backupPath }))
                }
              })
            })
          })
          return
        }

        res.statusCode = 405
        res.setHeader('Content-Type', 'application/json; charset=utf-8')
        res.end(JSON.stringify({ success: false, error: 'Method not allowed.' }))
      })
    },
  }
}

/**
 * Run-script API: POST /api/run-script
 * Body: { "script": "Get_and_Chunk/3.00chunker.py" }
 * Spawns the Python subprocess and responds IMMEDIATELY with { started: true }.
 * Progress and completion are read via GET /api/run-status — a previous
 * version held the HTTP request open for the entire (possibly hours-long)
 * run, so a dropped connection lost the result.
 */
function createRunScriptApi() {
  // Allowed script prefixes (relative to repo root)
  const allowedPrefixes = ['Get_and_Chunk/', 'VectorDB/']

  return {
    name: 'run-script-api',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url !== '/api/run-script' || req.method !== 'POST') {
          next()
          return
        }

        let body = ''
        req.on('data', chunk => { body += chunk })
        req.on('end', () => {
          let payload
          try {
            payload = JSON.parse(body)
          } catch {
            res.statusCode = 400
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: 'Invalid JSON body.' }))
            return
          }

          const script = payload.script
          if (!script || typeof script !== 'string') {
            res.statusCode = 400
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: 'Missing "script" field.' }))
            return
          }

          if (activeScriptProcesses.has(script)) {
            res.statusCode = 409
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: `Script is already running: ${script}` }))
            return
          }

          // Security: only allow scripts under known prefixes
          if (!allowedPrefixes.some(p => script.startsWith(p))) {
            res.statusCode = 403
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: `Script path not allowed: ${script}` }))
            return
          }

          const scriptPath = path.resolve(repoRoot, script)
          const normalized = path.normalize(scriptPath)
          if (!normalized.startsWith(repoRoot)) {
            res.statusCode = 403
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: 'Path traversal not allowed.' }))
            return
          }

          if (!fs.existsSync(normalized)) {
            res.statusCode = 404
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: `Script not found: ${script}` }))
            return
          }

          // Spawn Python process
          const pythonCmd = process.platform === 'win32' ? 'python' : 'python3'
          const launcherPath = path.resolve(repoRoot, 'run_python_entry.py')
          const child = spawn(pythonCmd, [launcherPath, normalized], {
            cwd: repoRoot,
            env: { ...process.env },
            stdio: ['ignore', 'pipe', 'pipe'],
          })
          activeScriptProcesses.set(script, child)

          const state = { output: '', exitCode: null, done: false, error: null, startedAt: Date.now() }
          scriptRunStates.set(script, state)

          const appendOutput = d => {
            state.output += d.toString()
            if (state.output.length > MAX_RUN_OUTPUT_CHARS) {
              state.output = state.output.slice(-MAX_RUN_OUTPUT_CHARS)
            }
          }
          child.stdout.on('data', appendOutput)
          child.stderr.on('data', appendOutput)

          child.on('error', err => {
            if (activeScriptProcesses.get(script) === child) {
              activeScriptProcesses.delete(script)
            }
            state.done = true
            state.error = `Failed to spawn: ${err.message}`
          })

          child.on('close', code => {
            if (activeScriptProcesses.get(script) === child) {
              activeScriptProcesses.delete(script)
            }
            state.exitCode = code
            state.done = true
          })

          // Respond immediately; the client polls /api/run-status.
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ success: true, started: true, script }))
        })
      })
    },
  }
}

/**
 * Run-status API: GET /api/run-status?script=Get_and_Chunk/3.00chunker.py
 * Returns the current/last run state for a script started via /api/run-script.
 */
function createRunStatusApi() {
  return {
    name: 'run-status-api',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url || !req.url.startsWith('/api/run-status') || req.method !== 'GET') {
          next()
          return
        }

        let parsed
        try {
          parsed = new URL(req.url, 'http://localhost')
        } catch {
          res.statusCode = 400
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ success: false, error: 'Invalid request URL.' }))
          return
        }

        const script = parsed.searchParams.get('script') || ''
        const state = scriptRunStates.get(script)
        res.setHeader('Content-Type', 'application/json')
        if (!state) {
          res.statusCode = 200
          res.end(JSON.stringify({ success: true, known: false }))
          return
        }
        res.statusCode = 200
        res.end(JSON.stringify({
          success: true,
          known: true,
          running: !state.done,
          done: state.done,
          exitCode: state.exitCode,
          error: state.error,
          output: state.output,
          startedAt: state.startedAt,
        }))
      })
    },
  }
}

/**
 * Stop-script API: POST /api/stop-script
 * Body: { "script": "Get_and_Chunk/1crawlweb.py" }
 * Stops a currently running script started through /api/run-script.
 */
function createStopScriptApi() {
  const allowedPrefixes = ['Get_and_Chunk/', 'VectorDB/']

  return {
    name: 'stop-script-api',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url !== '/api/stop-script' || req.method !== 'POST') {
          next()
          return
        }

        let body = ''
        req.on('data', chunk => { body += chunk })
        req.on('end', () => {
          let payload
          try {
            payload = JSON.parse(body || '{}')
          } catch {
            res.statusCode = 400
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: 'Invalid JSON body.' }))
            return
          }

          const script = payload.script
          if (!script || typeof script !== 'string') {
            res.statusCode = 400
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: 'Missing "script" field.' }))
            return
          }

          if (!allowedPrefixes.some(p => script.startsWith(p))) {
            res.statusCode = 403
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: `Script path not allowed: ${script}` }))
            return
          }

          const child = activeScriptProcesses.get(script)
          if (!child) {
            res.statusCode = 404
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: `No active process found for ${script}` }))
            return
          }

          let stopped = false
          try {
            stopped = child.kill('SIGTERM')
          } catch (err) {
            res.statusCode = 500
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: `Failed to stop process: ${err.message}` }))
            return
          }

          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ success: true, stopped }))
        })
      })
    },
  }
}

/**
 * Log-tail API: GET /api/log-tail?script=Get_and_Chunk/3.00chunker.py&lines=120
 * Returns the latest N lines from the script's CSV log file under LOG_DIR.
 * Only available during dev (apply: 'serve').
 */
function createLogTailApi() {
  const allowedPrefixes = ['Get_and_Chunk/', 'VectorDB/']

  function resolveLogDir() {
    const fallback = path.resolve(repoRoot, 'Logger', 'logs')
    const runSettingsPath = path.resolve(repoRoot, 'run_settings.py')
    if (!fs.existsSync(runSettingsPath)) {
      return fallback
    }

    try {
      const text = fs.readFileSync(runSettingsPath, 'utf8')
      const match = text.match(/LOG_DIR\s*=\s*Path\(\s*r?["']([^"']+)["']\s*\)/)
      if (!match || !match[1]) {
        return fallback
      }

      // run_settings.py may contain doubled backslashes in the literal text.
      const rawPath = String(match[1]).replace(/\\\\/g, '\\')
      if (path.isAbsolute(rawPath)) {
        return path.normalize(rawPath)
      }
      return path.resolve(repoRoot, rawPath)
    } catch {
      return fallback
    }
  }

  return {
    name: 'log-tail-api',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url || !req.url.startsWith('/api/log-tail') || req.method !== 'GET') {
          next()
          return
        }

        let parsed
        try {
          parsed = new URL(req.url, 'http://localhost')
        } catch {
          res.statusCode = 400
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ success: false, error: 'Invalid request URL.' }))
          return
        }

        const script = parsed.searchParams.get('script') || ''
        const linesParam = parsed.searchParams.get('lines')
        const lines = Math.max(1, Math.min(1000, Number.parseInt(linesParam || '120', 10) || 120))

        if (!script || typeof script !== 'string') {
          res.statusCode = 400
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ success: false, error: 'Missing "script" query parameter.' }))
          return
        }

        if (!allowedPrefixes.some(p => script.startsWith(p))) {
          res.statusCode = 403
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ success: false, error: `Script path not allowed: ${script}` }))
          return
        }

        const scriptBase = path.basename(script, path.extname(script))
        const logDir = resolveLogDir()
        const logPath = path.resolve(logDir, `a_${scriptBase}.log`)

        if (!logPath.startsWith(path.normalize(logDir))) {
          res.statusCode = 403
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ success: false, error: 'Computed log path is outside LOG_DIR.' }))
          return
        }

        if (!fs.existsSync(logPath)) {
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ success: true, exists: false, output: '' }))
          return
        }

        fs.readFile(logPath, 'utf8', (err, data) => {
          if (err) {
            res.statusCode = 500
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ success: false, error: `Unable to read log file: ${err.message}` }))
            return
          }

          const allLines = String(data || '').split(/\r?\n/)
          if (allLines.length > 0 && allLines[allLines.length - 1] === '') {
            allLines.pop()
          }
          const tail = allLines.slice(-lines).join('\n')

          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({
            success: true,
            exists: true,
            output: tail,
            lineCount: allLines.length,
          }))
        })
      })
    },
  }
}

export default defineConfig({
  root: '..', // your source is web/
  base: './',
  appType: 'mpa',
  plugins: [
    FullReload(['**/*.html']), // reload on ANY html change
    pipelineFileProxy(),
    pipelineWriteProxy(),
    getAndChunkFileProxy(),
    getAndChunkWriteProxy(),
    vectorDBFileProxy(),
    vectorDBWriteProxy(),
    createRunSettingsApi(),
    createRunScriptApi(),
    createRunStatusApi(),
    createStopScriptApi(),
    createLogTailApi(),
    copyStaticBuildResources(),
  ],
  define: {
    __APP_PATHS__: JSON.stringify(appPaths),
  },
  server: {
    port: 5173,
    watch: { usePolling: true, interval: 100 }, // fixes Windows/FS watchers
    fs: { allow: [projectRoot, pipelineDir, getAndChunkDir, vectorDBDir] },
  },
  build: { outDir: './build', emptyOutDir: true },
  test: {
    environment: 'jsdom',
    include: ['vite/tests/**/*.test.js'],
    globals: true,
  },
})

