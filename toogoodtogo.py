import os
import sys
import json
import requests
import smtplib
import random
import time
import datetime
import telegram
import base64
from config import config

class TooGoodToGo:
    def __init__(self):
        self.home = os.path.expanduser("~")
        self.cfgfile = "%s/.config/tgtgw/config.json" % self.home

        # default values
        self.config = {
            'email': None,
            'password': None,
            'accesstoken': None,
            'refreshtoken': None,
            'userid': "",
        }

        self.availables = {}
        self.baseurl = 'https://apptoogoodtogo.com'
        self.session = requests.session()

        self.colors = {
            'red': "\033[31;1m",
            'green': "\033[32;1m",
            'nc': "\033[0m",
        }

        self.bot = telegram.Bot(token=config['telegram-token'])

        # load configuration if exists
        self.load()

    # load configuration
    def load(self):
        if not os.path.exists(self.cfgfile):
            return False

        print("[+] loading configuration: %s" % self.cfgfile)
        with open(self.cfgfile, "r") as f:
            data = f.read()

        self.config = json.loads(data)

        print("[+] access token: %s" % self.config['accesstoken'])
        print("[+] refresh token: %s" % self.config['refreshtoken'])
        print("[+] user id: %s" % self.config['userid'])

    # save configuration
    def save(self):
        basepath = os.path.dirname(self.cfgfile)
        print("[+] configuration directory: %s" % basepath)

        if not os.path.exists(basepath):
            os.makedirs(basepath)

        with open(self.cfgfile, "w") as f:
            print("[+] writing configuration: %s" % self.cfgfile)
            f.write(json.dumps(self.config))

    def isauthorized(self, payload):
        if not payload.get("error"):
            return True

        if payload['error'] == 'Unauthorized':
            print("[-] request: unauthorized request")
            return False

        return None

    def url(self, endpoint):
        return "%s%s" % (self.baseurl, endpoint)

    def post(self, endpoint, json):
        headers = {
            'User-Agent': 'TooGoodToGo/20.1.1 (732) (iPhone/iPhone SE (GSM); iOS 13.3.1; Scale/2.00)',
            'Accept': "application/json",
            'Accept-Language': "en-US"
        }

        if self.config['accesstoken']:
            headers['Authorization'] = "Bearer %s" % self.config['accesstoken']

        return self.session.post(self.url(endpoint), headers=headers, json=json)

    def login(self):
        login = {
            'device_type': "UNKNOWN",
            'email': self.config['email'],
            'password': self.config['password']
        }

        # disable access token to request a new one
        self.config['accesstoken'] = None

        print("[+] authentication: login using <%s> email" % login['email'])

        r = self.post("/api/auth/v1/loginByEmail", login)
        data = r.json()

        if self.isauthorized(data) == False:
            print("[-] authentication: login failed, unauthorized")
            self.rawnotifier("Could not authenticate watcher, stopping.")
            sys.exit(1)

        self.config['accesstoken'] = data['access_token']
        self.config['refreshtoken'] = data['refresh_token']
        self.config['userid'] = data['startup_data']['user']['user_id']

        return True

    def refresh(self):
        data = {'refresh_token': self.config['refreshtoken']}
        ref = self.post('/api/auth/v1/token/refresh', data)

        payload = ref.json()
        if self.isauthorized(payload) == False:
            print("[-] authentication: refresh failed, re-loggin")
            return self.login()

        self.config['accesstoken'] = payload['access_token']

        print("[+] new token: %s" % self.config['accesstoken'])

        return True

    def favorite(self):
        data = {
            'favorites_only': True,
            'origin': {
                'latitude': config['latitude'],
                'longitude': config['longitude']
            },
            'radius': 200,
            'user_id': self.config['userid'],
            'page': 1,
            'page_size': 20
        }

        while True:
            try:
                r = self.post("/api/item/v4/", data)
                if r.status_code >= 500:
                    continue

                if r.status_code == 200:
                    return r.json()

            except Exception as e:
                print(r.text)
                print(e)

            time.sleep(1)


    def datetimeparse(self, datestr):
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        value = datetime.datetime.strptime(datestr, fmt)
        return value.replace(tzinfo=datetime.timezone.utc).astimezone(tz=None)

    def issameday(self, d1, d2):
        return (d1.day == d2.day and d1.month == d2.month and d1.year == d2.year)

    def pickupdate(self, item):
        now = datetime.datetime.now()
        pfrom = self.datetimeparse(item['pickup_interval']['start'])
        pto = self.datetimeparse(item['pickup_interval']['end'])

        prange = "%02d:%02d - %02d:%02d" % (pfrom.hour, pfrom.minute, pto.hour, pto.minute)

        if self.issameday(pfrom, now):
            return "Today, %s" % prange

        return "%d/%d, %s" % (pfrom.day, pfrom.month, prange)


    def available(self, items):
        for item in items['items']:
            name = item['display_name']
            price = item['item']['price']['minor_units'] / 100
            value = item['item']['value']['minor_units'] / 100
            color = "green" if item['items_available'] > 0 else "red"
            kname = "%s-%.2d" % (name, price)

            print("[+] merchant: %s%s%s" % (self.colors[color], name, self.colors['nc']))

            if item['items_available'] == 0:
                if self.availables.get(kname):
                    del self.availables[kname]

                continue

            print("[+]   distance: %.2f km" % item['distance'])
            print("[+]   available: %d" % item['items_available'])
            print("[+]   price: %.2f € [%.2f €]" % (price, value))
            print("[+]   address: %s" % item['pickup_location']['address']['address_line'])
            print("[+]   pickup: %s" % self.pickupdate(item))

            if not self.availables.get(kname):
                print("[+]")
                print("[+]   == NEW ITEMS AVAILABLE ==")
                self.notifier(item)
                self.availables[kname] = True


            print("[+]")

    #
    # STAGING BASKET / CHECKOUT
    #
    def basket(self, itemid):
        payload = {
            "supported_payment_providers": [
                {
                    "payment_provider": {
                        "provider_id": "VOUCHER",
                        "provider_version": 1
                    },
                    "payment_types": [
                        "VOUCHER"
                    ]
                },
                {
                    "payment_provider": {
                        "provider_id": "ADYEN",
                        "provider_version": 1
                    },
                    "payment_types": [
                        "CREDITCARD",
                        "PAYPAL",
                        "IDEAL",
                        "SOFORT",
                        "VIPPS",
                        "BCMCMOBILE",
                        "DOTPAY",
                        "APPLEPAY"
                    ]
                },
                {
                    "payment_provider": {
                        "provider_id": "PAYPAL",
                        "provider_version": 1
                    },
                    "payment_types": [
                        "PAYPAL"
                    ]
                }
            ],
            "user_id": self.config['userid']
        }

        r = self.post("/api/item/v4/%s/basket" % itemid, payload)
        data = r.json()

        if data['create_basket_state'] == 'SUCCESS':
            basketid = data['basket_id']
            print("[+] basket created: %s" % basketid)

            self.checkout(basketid)

        pass

    def checkout(self, basketid):
        now = datetime.datetime.now().replace(microsecond=0).isoformat() + "Z"

        paymentsdk = {
            "locale": "en_BE",
            "deviceIdentifier": "",
            "platform": "ios",
            "osVersion": "13.3.1",
            "integration": "quick",
            "sdkVersion": "2.8.5",
            "deviceFingerprintVersion": "1.0",
            "generationTime": now,
            "deviceModel": "iPhone8,4"
        }

        sdkkey = json.dumps(paymentsdk)

        payload = {
            "items_count": 1,
            "payment_provider": {
                "provider_id": "ADYEN",
                "provider_version": 1
            },
            "payment_sdk_key": base64.b64encode(sdkkey.encode('utf-8')),
            "payment_types": [
                "CREDITCARD",
                "APPLEPAY",
                "BCMCMOBILE",
                "PAYPAL"
            ],
            "return_url": "toogoodtogoapp://"
        }

        print(payload)

        r = self.post("/api/basket/v2/%s/checkout" % basketid, payload)
        data = r.json()

        print(data)

        if data['result'] == 'CONTINUE_PAYMENT':
            print("OK OK")

        pass

    def debug(self):
        self.basket("43351i2634099")
        print("debug")

    #
    #
    #

    def rawnotifier(self, message):
        fmt = telegram.ParseMode.MARKDOWN
        self.bot.send_message(chat_id=config['telegram-chat-id'], text=message, parse_mode=fmt)

    def notifier(self, item):
        name = item['display_name']
        items = item['items_available']
        price = item['item']['price']['minor_units'] / 100
        pickup = self.pickupdate(item)

        fmt = telegram.ParseMode.MARKDOWN
        message = "*%s*\n*Available*: %d\n*Price*: %.2f €\n*Pickup*: %s" % (name, items, price, pickup)

        self.bot.send_message(chat_id=config['telegram-chat-id'], text=message, parse_mode=fmt)

    def daytime(self):
        now = datetime.datetime.now()
        nowint = (now.hour * 100) + now.minute
        return nowint

    def watch(self):
        if self.config['accesstoken'] is None:
            self.login()
            self.save()

        while True:
            fav = self.favorite()
            if self.isauthorized(fav) == False:
                print("[-] favorites: unauthorized request, refreshing token")
                self.refresh()
                continue

            self.available(fav)

            #
            # night pause
            #
            now = self.daytime()

            if now >= config['night-pause-from'] or now <= config['night-pause-to']:
                print("[+] night mode enabled, fetching disabled")

                while now >= config['night-pause-from'] or now <= config['night-pause-to']:
                    now = self.daytime()
                    time.sleep(60)

                print("[+] starting new day")

            #
            # speedup or normal waiting time
            #
            waitfrom = config['normal-wait-from']
            waitto = config['normal-wait-to']

            if now >= config['speedup-time-from'] and now <= config['speedup-time-to']:
                print("[+] speedup time range enabled")
                waitfrom = config['speedup-wait-from']
                waitto = config['speedup-wait-to']

            #
            # next iteration
            #
            wait = random.randrange(waitfrom, waitto)
            print("[+] waiting %d seconds" % wait)
            time.sleep(wait)

        self.save()

if __name__ == '__main__':
    tgtg = TooGoodToGo()
    tgtg.watch()
