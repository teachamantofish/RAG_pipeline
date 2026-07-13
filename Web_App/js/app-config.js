(function (global) {
  'use strict';

  const CONFIG_JSON_PATH = './config/paths.json';
  const PYWEBVIEW_CONFIG_PATH = '../Web_App/config/paths.json';

  let configData = null;
  let configPromise = null;

  function isHttpProtocol() {
    if (!global || !global.location || !global.location.protocol) {
      return false;
    }
    const protocol = global.location.protocol;
    return protocol === 'http:' || protocol === 'https:';
  }

  function hasPywebview() {
    return !!(
      global &&
      global.pywebview &&
      global.pywebview.api &&
      typeof global.pywebview.api.load_file === 'function'
    );
  }

  async function fetchViaHttp() {
    if (typeof global.fetch !== 'function') {
      throw new Error('global fetch API unavailable');
    }

    const response = await global.fetch(CONFIG_JSON_PATH, { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} while fetching ${CONFIG_JSON_PATH}`);
    }
    return await response.json();
  }

  async function fetchViaPywebview() {
    const api = global && global.pywebview && global.pywebview.api;
    if (!api || typeof api.load_file !== 'function') {
      throw new Error('pywebview API unavailable');
    }
    const result = await api.load_file(PYWEBVIEW_CONFIG_PATH);
    if (!result || !result.success) {
      const error = result && result.error ? result.error : 'unknown error';
      throw new Error(`pywebview load_file failed: ${error}`);
    }
    try {
      return JSON.parse(result.content);
    } catch (err) {
      throw new Error(`Unable to parse JSON from ${PYWEBVIEW_CONFIG_PATH}: ${err.message}`);
    }
  }

  function resolveKey(obj, key) {
    if (!key) return obj;
    return key.split('.').reduce((acc, segment) => {
      if (acc && Object.prototype.hasOwnProperty.call(acc, segment)) {
        return acc[segment];
      }
      return undefined;
    }, obj);
  }

  async function loadConfig() {
    if (configData) return configData;

    const inlinePaths =
      typeof __APP_PATHS__ !== 'undefined'
        ? __APP_PATHS__
        : typeof global.__APP_PATHS__ !== 'undefined'
          ? global.__APP_PATHS__
          : null;

    if (inlinePaths) {
      configData = inlinePaths;
      global.__APP_PATHS__ = inlinePaths;
      return configData;
    }

    if (isHttpProtocol()) {
      try {
        configData = await fetchViaHttp();
        global.__APP_PATHS__ = configData;
        return configData;
      } catch (err) {
        console.warn('[app-config] HTTP fetch failed, falling back:', err);
      }
    }

    if (hasPywebview()) {
      configData = await fetchViaPywebview();
      global.__APP_PATHS__ = configData;
      return configData;
    }

    if (typeof global.fetch === 'function') {
      try {
        configData = await fetchViaHttp();
        global.__APP_PATHS__ = configData;
        return configData;
      } catch (err) {
        console.warn('[app-config] Final HTTP fetch attempt failed:', err);
      }
    }

    throw new Error('Unable to load application path configuration');
  }

  function ensureConfigPromise() {
    if (!configPromise) {
      configPromise = loadConfig().catch(err => {
        console.error('[app-config] Failed to load configuration:', err);
        throw err;
      });
    }
    return configPromise;
  }

  const AppConfig = {
    ready() {
      return ensureConfigPromise();
    },
    get(key) {
      if (!configData) {
        throw new Error('AppConfig not ready - call ready() first');
      }
      return key ? resolveKey(configData, key) : configData;
    },
    all() {
      if (!configData) {
        throw new Error('AppConfig not ready - call ready() first');
      }
      return configData;
    },
  };

  global.AppConfig = AppConfig;
  ensureConfigPromise();
})(typeof window !== 'undefined' ? window : globalThis);
