const url = require('url');

const l = {};

l.listen = (page, ignoreHostnames, validHttpCodes) => {
  page.on('response', resp => {
    if (! resp.ok() && validHttpCodes.indexOf(resp.status()) === -1) {
      console.log(`${resp.url()} failed from unusual HTTP response code: ${resp.status()}`);
    }
  });

  page.on('requestfailed', req => {
    const hostname = url.parse(req.url()).hostname;
    if (! ignoreHostnames.includes(hostname)) {
      console.log(req.url() + ' ' + req.failure().errorText);
    }
  });

  page.on('requestfinished', req => {
    // console.log('request completed: ' + req.url());
  });
};

module.exports = l;
