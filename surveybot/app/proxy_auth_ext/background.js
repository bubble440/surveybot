const USERNAME = "brd-customer-...";
const PASSWORD = "*******";

chrome.webRequest.onAuthRequired.addListener(
  function(details) {
  return {authCredentials: {username: USERNAME, password: PASSWORD}};
  },
  {urls: ["<all_urls>"]},
  ["blocking", "extraHeaders"]
);
