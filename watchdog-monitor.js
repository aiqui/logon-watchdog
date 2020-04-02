#!/usr/bin/env node

const puppeteer = require('puppeteer');
const performance = require('perf_hooks').performance;
const fs = require('fs');
const path = require('path');

const listener = require('./src/listener');
const cookies = require('./src/cookies');


/**
 * Retry upon failure with a max timeout.
 */
const retry = (fn, ms = 1000, maxRetries = 5) =>
    new Promise((resolve, reject) => {
        let retries = 0;
        fn()
            .then(resolve)
            .catch(() => {
                setTimeout(() => {
                    statusMsg('retrying failed promise...');
                    ++retries;
                    if (retries === maxRetries) {
                        return reject('maximum retries exceeded');
                    }
                    retry(fn, ms).then(resolve);
                }, ms);
            });
    });

const errorMsg = async (msg, exitCode = -1) => {
    console.error(`  watchdog-monitor: ${msg}`);
    if (exitCode) {
        process.exit(exitCode);
    }
};

const statusMsg = async (msg) => {
    console.log(`  watchdog-monitor: ${msg}`);
};

const isDir = async (path) => {
    try {
        const stat = await fs.lstatSync(path);
        return stat.isDirectory();
    } catch (e) {
        // lstatSync throws an error if path doesn't exist
        return false;
    }
};

const saveScreen = async (page, prefix, logDir, saveContent) => {
    const imageFile = `${logDir}/${prefix}.png`,
          contentFile = `${logDir}/${prefix}.html`;
    statusMsg(`saving screenshot to: ${imageFile}`);
    await page.screenshot({path: imageFile});
    if (saveContent) {
        statusMsg(`saving content to: ${contentFile}`);
        await fs.writeFileSync(contentFile, await page.content());
    }
};

const displayFormat = async () => {
    const prog = path.basename(__filename);
    console.error(`Format: ${prog} LOGDIR`);
    process.exit(-1);
};

/**
 * This is the heart of the monitoring system.
 * The path is va.gov -> id.me -> DSLogon (username, password) -> id.me (MFA) -> va.gov
 */
(async () => {
    const config = require('./config.json');
    const startUrl = config.start.url;
    const username = config.authentication.username;
    const password = config.authentication.password;

    if (process.argv.length !== 3) {
        await displayFormat();
    }

    const logDir = process.argv[2];
    if (! await isDir(logDir)) {
        console.error(`Error: invalid log directory: ${logDir}\n`);
        await displayFormat();
    }

    // ignore HTTPS errors due to certificate errors
    const browser = await puppeteer.launch({
        ignoreHTTPSErrors: true,
        headless: true,
        slowMo: 100, // Counter refreshes by simulating human behavior of ~100ms.
        args: ['--start-fullscreen']
    });

    const start = performance.now();

    // Setup a listener for unhandled promise rejections
    process.on('unhandledRejection', (reason) => {
        errorMsg(`an unhandled rejection with reason: ${reason}`);
    });

    const pages = await browser.pages();
    let page = pages[0];

    // Set up the listener with the list of hostnames to ignore and valid codes
    listener.listen(page, config.listener.ignore_hostnames, config.listener.valid_codes);

    // Read cookies if enabled
    if (config.cookies.active) {
        await cookies.read(page, config.cookies.path, statusMsg);
    }

    statusMsg(`entering website: ${startUrl}`);

    // set the initial login to high timeout, along with retry.
    page.setDefaultNavigationTimeout(config.timeouts.default * 1000);
    await page.setViewport({width: 1920, height: 900});
    const REQ_TIMEOUT_MS = config.timeouts.request * 1000;
    const MAX_RETRY = 5;
    const response = await retry(() => page.goto(startUrl), REQ_TIMEOUT_MS, MAX_RETRY);

    statusMsg(`starting page response: ${response.status()}`);

    const userIdSelector = config.login.selectors.user_id;
    const passwordSelector = config.login.selectors.password;
    const loginButtonSelector = config.login.selectors.login_btn;
    const landingSelector = config.end.selectors.common;

    statusMsg('waiting for login (no cookie) or common landing (valid cookies) elements');
    try {
        // Wait for the user ID or landing-page elements
        await page.waitForFunction((userIdSelector, landingSelector) => {
            return document.querySelector(userIdSelector) || document.querySelector(landingSelector);
        }, { timeout: config.timeouts.login * 1000 }, userIdSelector, landingSelector);
    } catch (e) {
        console.error(e.toString());
        await saveScreen(page, 'login-failed', logDir, true);
        await errorMsg(`login page did not appear - final URL: ${page.url()}`);
    }

    // Login selector is valid
    if (await page.$(userIdSelector)) {
        if (!page.url().includes(config.login.url)) {
            await saveScreen(page, 'login-failed', logDir, true);
            await errorMsg(`login url "${page.url()}" did not match "${config.login.url}"`);
        }

        statusMsg('validating all login elements');
        [userIdSelector, passwordSelector, loginButtonSelector].forEach((selector, errorMsg) => {
            if (!page.$(selector)) {
                errorMsg(`login page missing element - unable to find: ${selector}`);
            }
        });

        // Login using the user ID, password and login button
        statusMsg('logging in');
        await page.click(userIdSelector);
        await page.type(userIdSelector, username);
        await page.click(passwordSelector);
        await page.type(passwordSelector, password);
        await page.click(loginButtonSelector);
    } else {
        statusMsg(`appear to already be logged in, URL: ${page.url()}`);
    }

    // Wait for the landing element - should be found in all cases
    try {
        await page.waitForSelector(landingSelector, {timeout: config.timeouts.end * 1000});
        statusMsg(`ending selector found: "${landingSelector}"`);
    } catch (e) {
        statusMsg(e.toString());
        await saveScreen(page, 'end-failed', logDir, true);
        await errorMsg(`ending selector did not appear ${landingSelector} - final URL: ${page.url()}`);
    }

    if (page.url().includes(config.end.url)) {
        statusMsg(`successfully reached ending URL: ${config.end.url}`);
    } else {
        await saveScreen(page, 'end-failed', logDir, true);
        await errorMsg(`end page did not appear - final URL: ${page.url()}`);
    }

    // Save cookies if enabled
    if (config.cookies.active) {
        await cookies.write(page, config.cookies.path, config.cookies.urls, statusMsg);
    }

    await browser.close();

    // Track the final timing of the application run
    // This is the user end-to-end experience for time from logging in to logging off.
    let end = performance.now();
    let timeInMs = end - start;

    const timeToComplete = Number((timeInMs / 1000).toFixed(1));
    statusMsg(`completed web session. Took ${timeToComplete} seconds.`);

})();
