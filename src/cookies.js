const fs = require('fs');

const l = {};

// Read cookies from the cookie file
l.read = async (page, cookiesPath) => {
    if (fs.existsSync(cookiesPath)) {
        const cookies = JSON.parse(await fs.readFileSync(cookiesPath));
        for (let cookie of cookies) {
            await page.setCookie(cookie);
        }
        console.log(`${cookies.length} cookies loaded from ${cookiesPath}`);
    }
};

// Write cookies of certain URLs to a file
l.write = async (page, cookiesPath, urls) => {
    let allCookies = [];
    for (const url of urls) {
        const cookies = await page.cookies(url);
        if (cookies) {
            allCookies.push.apply(allCookies, cookies);
        }
    }
    await fs.writeFileSync(cookiesPath, JSON.stringify(allCookies));
    console.log(`${allCookies.length} cookies saved to ${cookiesPath}`);
};

module.exports = l;
