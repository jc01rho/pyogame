# coding: utf-8
import datetime
import json
import math
import re
import time
import arrow
import urllib3
import requests
import json
import pickle
import random
from xml.dom import minidom

from ogame import constants
from ogame.errors import BAD_UNIVERSE_NAME, BAD_DEFENSE_ID, NOT_LOGGED, BAD_CREDENTIALS, CANT_PROCESS, BAD_BUILDING_ID, \
    BAD_SHIP_ID, BAD_RESEARCH_ID
from bs4 import BeautifulSoup
from dateutil import tz

miniFleetToken = None


def update_cookies(session_dict):
    pickle.dump(session_dict, open("save.txt", "wb"))


def set_mini_fleet_token(token):
    global miniFleetToken  # Needed to modify global copy of globvar
    miniFleetToken = token


def parse_int(text):
    try:
        return int(text.replace('.', '').replace(',', '').strip())
    except ValueError:
        return 1


def for_all_methods(decorator):
    def decorate(cls):
        for attr in cls.__dict__:  # there's propably a better way to do this
            if callable(getattr(cls, attr)):
                setattr(cls, attr, retry_if_logged_out(decorator(getattr(cls, attr))))
        return cls

    return decorate


def sandbox_decorator(some_fn):
    def wrapper(ogame, *args, **kwargs):
        fn_name = some_fn.__name__

        local_fns = ['get_datetime_from_time']

        if fn_name in local_fns:
            return some_fn(ogame, *args, **kwargs)

        if fn_name == '__init__' or not ogame.sandbox:
            return some_fn(ogame, *args, **kwargs)

        if fn_name in ogame.sandbox_obj:
            return ogame.sandbox_obj[fn_name]

        return None

    return wrapper


def retry_if_logged_out(method):
    def wrapper(self, *args, **kwargs):
        attempt = 0
        time_to_sleep = 0
        working = False
        while not working:
            try:
                working = True
                res = method(self, *args, **kwargs)
            except NOT_LOGGED:
                time.sleep(time_to_sleep)
                attempt += 1
                time_to_sleep += 1
                if attempt > 5:
                    raise CANT_PROCESS
                working = False
                self.login()
        return res

    return wrapper


def get_nbr(soup, name):
    div = soup.find('div', {'class': name})
    level = div.find('span', {'class': 'level'})
    for tag in level.findAll(True):
        tag.extract()
    return parse_int(level.text)


def metal_mine_production(level, universe_speed=1):
    return int(math.floor(30 * level * 1.1 ** level) * universe_speed)


def get_planet_infos_regex(text):
    result = re.search(r'(\w+) \[(\d+):(\d+):(\d+)\]([\d\.]+)km \((\d+)/(\d+)\)([-\d]+).+C (?:bis|à|to) ([-\d]+).+C',
                       text)
    if result is not None:
        return result  # is a plenet
    else:
        return re.search(r'(\w+) \[(\d+):(\d+):(\d+)\]([\d\.]+)km \((\d+)/(\d+)\)', text)  # is a moon


def get_code(name):
    if name in constants.Buildings.keys():
        return constants.Buildings[name]
    if name in constants.Facilities.keys():
        return constants.Facilities[name]
    if name in constants.Defense.keys():
        return constants.Defense[name]
    if name in constants.Ships.keys():
        return constants.Ships[name]
    if name in constants.Research.keys():
        return constants.Research[name]
    print('Couldn\'t find code for {}'.format(name))
    return None







@for_all_methods(sandbox_decorator)
class OGame(object):
    def __init__(self, universe, universe_id, universe_lang, universe_url, username, password,
                 domain='en.ogame.gameforge.com',
                 auto_bootstrap=True,
                 sandbox=False, sandbox_obj=None, use_proxy=False, proxy_port=9050):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = requests.session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.67 Safari/537.36'})
        self.sandbox = sandbox
        self.sandbox_obj = sandbox_obj if sandbox_obj is not None else {}
        self.universe = universe
        self.universe_id = universe_id
        self.universe_lang = universe_lang
        self.universe_url = universe_url
        self.domain = domain
        self.username = username
        self.password = password
        self.universe_speed = 1
        self.server_url = ''
        self.server_tz = self.getServerTimezone()
        if auto_bootstrap:
            self.login()
            self.universe_speed = self.get_universe_speed()
        if use_proxy:
            self.session.proxies.update(get_proxies(proxy_port))

    def getServerTimezone(self):
        # https://s800-en.ogame.gameforge.com/api/serverData.xml
        # requests.get(url, proxies=get_proxies())
        #https://s152-en.ogame.gameforge.com/api/serverData.xml
        tree = minidom.parseString(requests.get("https://" + self.universe_url + "/api/serverData.xml").content)

        return tree.getElementsByTagName("timezoneOffset")[0].firstChild.nodeValue

    def login(self):
        """Get the ogame session token."""
        if self.server_url == '':
            self.server_url = self.get_universe_url(self.universe)

        # 1 login to lobby
        payload = {'kid': '',
                   'language': self.universe_lang,
                   'autologin': 'false',
                   'credentials[email]': self.username,
                   'credentials[password]': self.password}

        time.sleep(random.uniform(1, 2))
        res = self.session.post('https://lobby.ogame.gameforge.com/api/users', data=payload)

        php_session_id = None
        for c in res.cookies:
            if c.name == 'PHPSESSID':
                php_session_id = c.value
                break
        cookie = {'PHPSESSID': php_session_id}

        res = self.session.get('https://lobby.ogame.gameforge.com/api/servers').json()
        server_num = None
        for server in res:
            name = server['name'].lower()
            if self.universe.lower() == name:
                server_num = server['number']
                break

        res = self.session.get('https://lobby.ogame.gameforge.com/api/users/me/accounts', cookies=cookie)
        selected_server_id = None
        lang = None
        server_accounts = res.json()
        for server_account in server_accounts:
            if server_account['server']['number'] == server_num:
                lang = server_account['server']['language']
                selected_server_id = server_account['id']
                break

        time.sleep(random.uniform(1, 2))
        res = self.session.get('https://lobby.ogame.gameforge.com/api/users/me/loginLink?id={}&server[language]={}&server[number]={}'
                .format(selected_server_id, lang, str(server_num)), cookies=cookie).json()
        selected_server_url = res['url']
        b = re.search('https://(.+\.ogame\.gameforge\.com)/game', selected_server_url)
        self.server_url = b.group(1)

        res = self.session.get(selected_server_url).content
        soup = BeautifulSoup(res, 'html.parser')
        session_found = soup.find('meta', {'name': 'ogame-session'})
        if session_found:
            self.ogame_session = session_found.get('content')
            # Save the session to a file
            session_dict = self.session.cookies
            update_cookies(session_dict)
        else:
            raise BAD_CREDENTIALS

    def logout(self):
        self.session.get(self.get_url('logout'))

    def is_logged(self, html=None):
        if not html:
            html = self.session.get(self.get_url('overview')).content
        soup = BeautifulSoup(html, 'html.parser')
        session = soup.find('meta', {'name': 'ogame-session'})
        return session is not None

    def get_page_content(self, page='overview', cp=None):
        """Return the html of a specific page."""
        payload = {}
        if cp is not None:
            payload.update({'cp': cp})
        html = self.session.get(self.get_url(page, payload)).content
        if not self.is_logged(html):
            raise NOT_LOGGED
        return html

    def fetch_eventbox(self):
        res = self.session.get(self.get_url('fetchEventbox')).content.decode('utf8')
        try:
            obj = json.loads(res)
        except ValueError:
            raise NOT_LOGGED
        return obj

    def fetch_resources(self, planet_id):
        url = self.get_url('fetchResources', {'cp': planet_id})
        res = self.session.get(url).content.decode('utf8')
        try:
            obj = json.loads(res)
        except ValueError:
            raise NOT_LOGGED
        return obj

    def get_resources(self, planet_id):
        """Returns the planet resources stats."""
        resources = self.fetch_resources(planet_id)
        metal = resources['metal']['resources']['actual']
        max_metal = resources['metal']['resources']['max']
        crystal = resources['crystal']['resources']['actual']
        max_crystal = resources['crystal']['resources']['max']
        deuterium = resources['deuterium']['resources']['actual']
        max_deuterium = resources['deuterium']['resources']['max']
        energy = resources['energy']['resources']['actual']
        darkmatter = resources['darkmatter']['resources']['actual']
        result = {'metal': metal, 'crystal': crystal, 'deuterium': deuterium,
                  'energy': energy, 'darkmatter': darkmatter, 'max_metal': max_metal, 'max_crystal': max_crystal,
                  'max_deuterium': max_deuterium}
        return result

    def get_universe_speed(self, res=None):
        if not res:
            res = self.session.get(self.get_url('techtree', {'tab': 2, 'techID': 1})).content
        soup = BeautifulSoup(res, 'html.parser')
        if soup.find('head'):
            raise NOT_LOGGED
        spans = soup.findAll('span', {'class': 'undermark'})
        level = parse_int(spans[0].text)
        val = parse_int(spans[1].text)
        metal_production = metal_mine_production(level, 1)
        universe_speed = val / metal_production
        return universe_speed

    def get_user_infos(self, html=None):
        if not html:
            html = self.session.get(self.get_url('overview')).content
        if not self.is_logged(html):
            raise NOT_LOGGED
        res = {}
        res['player_id'] = int(re.search(r'playerId="(\w+)"', str(html)).group(1))
        res['player_name'] = re.search(r'playerName="([\w\s]+)"', str(html)).group(1)
        tmp = re.search(r'textContent\[7\]="([^"]+)"', str(html)).group(1)
        soup = BeautifulSoup(tmp, 'html.parser')
        tmp = soup.text
        infos = re.search(r'([\d\\.]+) \(\S+ ([\d\.]+) \S+ ([\d\.]+)\)', tmp)
        res['points'] = parse_int(infos.group(1))
        res['rank'] = parse_int(infos.group(2))
        res['total'] = parse_int(infos.group(3))
        res['honour_points'] = parse_int(re.search(r'textContent\[9\]="([^"]+)"', str(html)).group(1))
        res['planet_ids'] = self.get_planet_ids(html)
        res['current_planets'] = re.search(r'(\d+)/(\d+)',
                                           BeautifulSoup(html, 'lxml').find('p', 'textCenter').text).group(1)
        res['max_planets'] = re.search(r'(\d+)/(\d+)',
                                       BeautifulSoup(html, 'lxml').find('p', 'textCenter').text).group(2)
        return res

    def get_resources_buildings(self, planet_id):
        res = self.session.get(self.get_url('resources', {'cp': planet_id})).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        res = {}
        res['metal_mine'] = get_nbr(soup, 'supply1')
        res['crystal_mine'] = get_nbr(soup, 'supply2')
        res['deuterium_synthesizer'] = get_nbr(soup, 'supply3')
        res['solar_plant'] = get_nbr(soup, 'supply4')
        res['fusion_reactor'] = get_nbr(soup, 'supply12')
        res['solar_satellite'] = get_nbr(soup, 'supply212')
        res['metal_storage'] = get_nbr(soup, 'supply22')
        res['crystal_storage'] = get_nbr(soup, 'supply23')
        res['deuterium_tank'] = get_nbr(soup, 'supply24')
        return res

    def get_defense(self, planet_id):
        res = self.session.get(self.get_url('defense', {'cp': planet_id})).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        res = {}
        res['rocket_launcher'] = get_nbr(soup, 'defense401')
        res['light_laser'] = get_nbr(soup, 'defense402')
        res['heavy_laser'] = get_nbr(soup, 'defense403')
        res['gauss_cannon'] = get_nbr(soup, 'defense404')
        res['ion_cannon'] = get_nbr(soup, 'defense405')
        res['plasma_turret'] = get_nbr(soup, 'defense406')
        res['small_shield_dome'] = get_nbr(soup, 'defense407')
        res['large_shield_dome'] = get_nbr(soup, 'defense408')
        res['anti_ballistic_missiles'] = get_nbr(soup, 'defense502')
        res['interplanetary_missiles'] = get_nbr(soup, 'defense503')
        return res

    def get_ships(self, planet_id):
        res = self.session.get(self.get_url('shipyard', {'cp': planet_id})).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        res = {}
        res['light_fighter'] = get_nbr(soup, 'military204')
        res['heavy_fighter'] = get_nbr(soup, 'military205')
        res['cruiser'] = get_nbr(soup, 'military206')
        res['battleship'] = get_nbr(soup, 'military207')
        res['battlecruiser'] = get_nbr(soup, 'military215')
        res['bomber'] = get_nbr(soup, 'military211')
        res['destroyer'] = get_nbr(soup, 'military213')
        res['deathstar'] = get_nbr(soup, 'military214')
        res['small_cargo'] = get_nbr(soup, 'civil202')
        res['large_cargo'] = get_nbr(soup, 'civil203')
        res['colony_ship'] = get_nbr(soup, 'civil208')
        res['recycler'] = get_nbr(soup, 'civil209')
        res['espionage_probe'] = get_nbr(soup, 'civil210')
        res['solar_satellite'] = get_nbr(soup, 'civil212')
        return res

    def get_facilities(self, planet_id):
        res = self.session.get(self.get_url('station', {'cp': planet_id})).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        res = {}
        res['robotics_factory'] = get_nbr(soup, 'station14')
        res['shipyard'] = get_nbr(soup, 'station21')
        res['research_lab'] = get_nbr(soup, 'station31')
        res['alliance_depot'] = get_nbr(soup, 'station34')
        res['missile_silo'] = get_nbr(soup, 'station44')
        res['nanite_factory'] = get_nbr(soup, 'station15')
        res['terraformer'] = get_nbr(soup, 'station33')
        res['space_dock'] = get_nbr(soup, 'station36')
        return res

    def get_research(self):
        res = self.session.get(self.get_url('research')).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        res = {}
        res['energy_technology'] = get_nbr(soup, 'research113')
        res['laser_technology'] = get_nbr(soup, 'research120')
        res['ion_technology'] = get_nbr(soup, 'research121')
        res['hyperspace_technology'] = get_nbr(soup, 'research114')
        res['plasma_technology'] = get_nbr(soup, 'research122')
        res['combustion_drive'] = get_nbr(soup, 'research115')
        res['impulse_drive'] = get_nbr(soup, 'research117')
        res['hyperspace_drive'] = get_nbr(soup, 'research118')
        res['espionage_technology'] = get_nbr(soup, 'research106')
        res['computer_technology'] = get_nbr(soup, 'research108')
        res['astrophysics'] = get_nbr(soup, 'research124')
        res['intergalactic_research_network'] = get_nbr(soup, 'research123')
        res['graviton_technology'] = get_nbr(soup, 'research199')
        res['weapons_technology'] = get_nbr(soup, 'research109')
        res['shielding_technology'] = get_nbr(soup, 'research110')
        res['armour_technology'] = get_nbr(soup, 'research111')
        return res

    def constructions_being_built(self, planet_id):
        res = self.session.get(self.get_url('overview', {'cp': planet_id})).text
        if not self.is_logged(res):
            raise NOT_LOGGED
        buildingCountdown = 0
        buildingID = 0
        researchCountdown = 0
        researchID = 0
        buildingCountdownMatch = re.search('getElementByIdWithCache\("Countdown"\),(\d+),', res)
        if buildingCountdownMatch:
            buildingCountdown = buildingCountdownMatch.group(1)
            buildingID = re.search('onclick="cancelProduction\((\d+),', res).group(1)
        researchCountdownMatch = re.search('getElementByIdWithCache\("researchCountdown"\),(\d+),', res)
        if researchCountdownMatch:
            researchCountdown = researchCountdownMatch.group(1)
            researchID = re.search('onclick="cancelResearch\((\d+),', res).group(1)
        return buildingID, buildingCountdown, researchID, researchCountdown

    def is_under_attack(self, json_obj=None):
        if not json_obj:
            json_obj = self.fetch_eventbox()
        return not json_obj.get('hostile', 0) == 0

    def get_planet_ids(self, res=None):
        """Get the ids of your planets."""
        if not res:
            res = self.session.get(self.get_url('overview')).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        planets = soup.findAll('div', {'class': 'smallplanet'})
        ids = [planet['id'].replace('planet-', '') for planet in planets]
        return ids

    def get_moon_ids(self, res=None):
        """Get the ids of your moons."""
        if not res:
            res = self.session.get(self.get_url('overview')).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        moons = soup.findAll('a', {'class': 'moonlink'})
        ids = [moon['href'].split('&cp=')[1] for moon in moons]
        return ids

    def get_planet_by_name(self, planet_name, res=None):
        """Returns the first planet id with the specified name."""
        if not res:
            res = self.session.get(self.get_url('overview')).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        planets = soup.findAll('div', {'class': 'smallplanet'})
        for planet in planets:
            title = planet.find('a', {'class': 'planetlink'}).get('title')
            name = re.search(r'<b>(.+) \[(\d+):(\d+):(\d+)\]</b>', title).groups()[0]
            if name == planet_name:
                planet_id = planet['id'].replace('planet-', '')
                return planet_id
        return None

    def build_defense(self, planet_id, defense_id, nbr):
        """Build a defense unit."""
        if defense_id not in constants.Defense.values():
            raise BAD_DEFENSE_ID

        url = self.get_url('defense', {'cp': planet_id})

        res = self.session.get(url).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        form = soup.find('form')
        token = form.find('input', {'name': 'token'}).get('value')

        payload = {'menge': nbr,
                   'modus': 1,
                   'token': token,
                   'type': defense_id}
        self.session.post(url, data=payload)

    def get_shipyard_queueSize(self, planet_id):
        """Build a ship unit."""

        url = self.get_url('shipyard', {'cp': planet_id})

        res = self.session.get(url).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'lxml')
        items = soup.find_all("li", {"class": "tooltip"})
        return items.__len__()

    def get_shipyard_queue(self, planet_id):
        """Build a ship unit."""

        url = self.get_url('shipyard', {'cp': planet_id})

        res = self.session.get(url).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'lxml')
        items = soup.find_all("li", {"class": "tooltip"})
        resultList = []

        nowOnItems = soup.find("td", {"class": "building tooltip"})
        if nowOnItems == None:
            return resultList

        items.insert(0, nowOnItems)

        for item in items:
            resultDict = {}
            if item == None:
                continue;

            itemCount = item.span.string if item.span != None else item.div.string
            itemID = item.a.get('ref')

            if itemID is None:
                itemID = item.a.get('href').split('openTech=')[1]
            resultDict.update({'id': itemID})
            resultDict.update({'count': itemCount})

            if item.attrs['class'][0] == "building":
                resultDict.update({'onProgress': True})
            else:
                resultDict.update({'onProgress': False})

            resultList.append(resultDict)

        return resultList

    def build_ships(self, planet_id, ship_id, nbr):
        """Build a ship unit."""
        if ship_id not in constants.Ships.values():
            raise BAD_SHIP_ID

        url = self.get_url('shipyard', {'cp': planet_id})

        res = self.session.get(url).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        form = soup.find('form')
        token = form.find('input', {'name': 'token'}).get('value')

        payload = {'menge': nbr,
                   'modus': 1,
                   'token': token,
                   'type': ship_id}
        self.session.post(url, data=payload)

    def build_building(self, planet_id, building_id, cancel=False):
        """Build a building."""
        if building_id not in constants.Buildings.values() and building_id not in constants.Facilities.values():
            raise BAD_BUILDING_ID

        url = self.get_url('resources', {'cp': planet_id})

        res = self.session.get(url).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        # is_idle = bool(soup.find('td', {'class': 'idle'}))
        # if not is_idle:
        #     return False
        form = soup.find('form')
        token = form.find('input', {'name': 'token'}).get('value')
        modus = 2 if cancel else 1
        payload = {'modus': modus,
                   'token': token,
                   'type': building_id}
        self.session.post(url, data=payload)
        # return True

    def build_technology(self, planet_id, technology_id, cancel=False):
        if technology_id not in constants.Research.values():
            raise BAD_RESEARCH_ID

        url = self.get_url('research', {'cp': planet_id})
        modus = 2 if cancel else 1
        payload = {'modus': modus,
                   'type': technology_id}
        res = self.session.post(url, data=payload).content
        if not self.is_logged(res):
            raise NOT_LOGGED

    def _build(self, planet_id, object_id, nbr=None, cancel=False):
        if object_id in constants.Buildings.values() or object_id in constants.Facilities.values():
            self.build_building(planet_id, object_id, cancel=cancel)
        elif object_id in constants.Research.values():
            self.build_technology(planet_id, object_id, cancel=cancel)
        elif object_id in constants.Ships.values():
            self.build_ships(planet_id, object_id, nbr)
        elif object_id in constants.Defense.values():
            self.build_defense(planet_id, object_id, nbr)

    def build(self, planet_id, arg, cancel=False):
        if isinstance(arg, list):
            for element in arg:
                self.build(planet_id, element, cancel=cancel)
        elif isinstance(arg, tuple):
            elem_id, nbr = arg
            self._build(planet_id, elem_id, nbr, cancel=cancel)
        else:
            elem_id = arg
            self._build(planet_id, elem_id, cancel=cancel)

    def send_fleet(self, planet_id, ships, speed, where, mission, resources, acsDefHoldTime=0):
        def get_hidden_fields(html):
            soup = BeautifulSoup(html, 'html.parser')
            inputs = soup.findAll('input', {'type': 'hidden'})
            fields = {}
            for input_element in inputs:
                name = input_element.get('name')
                value = input_element.get('value')
                fields[name] = value
            return fields

        url = self.get_url('fleet1', {'cp': planet_id})

        res = self.session.get(url).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        payload = {}
        payload.update(get_hidden_fields(res))
        for name, value in ships:
            payload['am{}'.format(name)] = value
        res = self.session.post(self.get_url('fleet2'), data=payload).content

        payload = {}
        payload.update(get_hidden_fields(res))
        payload.update({'speed': speed,
                        'galaxy': where.get('galaxy'),
                        'system': where.get('system'),
                        'position': where.get('position'),
                        'type': where.get('type', 1)})
        if mission == constants.Missions['RecycleDebrisField']:
            # planet type: 1
            # debris type: 2
            # moon type: 3
            payload.update({'type': 2})  # Send to debris field
        res = self.session.post(self.get_url('fleet3'), data=payload).content

        payload = {}
        payload.update(get_hidden_fields(res))
        payload.update({'crystal': resources.get('crystal'),
                        'deuterium': resources.get('deuterium'),
                        'metal': resources.get('metal'),
                        'mission': mission,
                        'holdingtime': acsDefHoldTime
                        })
        res = self.session.post(self.get_url('movement'), data=payload).content

        res = self.session.get(self.get_url('movement')).content
        soup = BeautifulSoup(res, 'html.parser')
        origin_coords = soup.find('meta', {'name': 'ogame-planet-coordinates'})['content']
        fleets = soup.findAll('div', {'class': 'fleetDetails'})
        matches = []
        for fleet in fleets:
            origin = fleet.find('span', {'class': 'originCoords'}).text
            dest = fleet.find('span', {'class': 'destinationCoords'}).text
            reversal_span = fleet.find('span', {'class': 'reversal'})
            if not reversal_span:
                continue
            fleet_id = int(reversal_span.get('ref'))
            if dest == '[{}:{}:{}]'.format(where['galaxy'], where['system'], where['position']) and origin == '[{}]'.format(origin_coords):
                matches.append(fleet_id)
        if matches:
            return max(matches)
        return None

    def cancel_fleet(self, fleet_id):
        res = self.session.get(self.get_url('movement') + '&return={}'.format(fleet_id)).content
        if not self.is_logged(res):
            raise NOT_LOGGED

    def get_fleets(self):
        res = self.session.get(self.get_url('movement')).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        fleets = []
        soup = BeautifulSoup(res, 'html.parser')
        divs = soup.findAll('div', {'class': 'fleetDetails'})
        for div in divs:
            originText = div.find('span', {'class': 'originCoords'}).find('a').text
            coords = re.search(r'\[(\d+):(\d+):(\d+)\]', originText)
            galaxy, system, position = coords.groups()
            origin = (int(galaxy), int(system), int(position))
            destText = div.find('span', {'class': 'destinationCoords'}).find('a').text
            coords = re.search(r'\[(\d+):(\d+):(\d+)\]', destText)
            galaxy, system, position = coords.groups()
            dest = (int(galaxy), int(system), int(position))
            reversal_id = None
            reversal_span = div.find('span', {'class': 'reversal'})
            if reversal_span:
                reversal_id = int(reversal_span.get('ref'))
            mission_type = int(div.get('data-mission-type'))
            return_flight = bool(div.get('data-return-flight'))
            arrival_time = int(div.get('data-arrival-time'))
            ogameTimestamp = int(soup.find('meta', {'name': 'ogame-timestamp'})['content'])
            secs = arrival_time - ogameTimestamp
            if secs < 0: secs = 0
            trs = div.find('table', {'class': 'fleetinfo'}).findAll('tr')
            metal = parse_int(trs[-3].findAll('td')[1].text.strip())
            crystal = parse_int(trs[-2].findAll('td')[1].text.strip())
            deuterium = parse_int(trs[-1].findAll('td')[1].text.strip())
            fleet = {
                'id': reversal_id,
                'origin': origin,
                'destination': dest,
                'mission': mission_type,
                'return_flight': return_flight,
                'arrive_in': secs,
                'resources': {
                    'metal': metal,
                    'crystal': crystal,
                    'deuterium': deuterium,
                },
                'ships': {
                    'light_fighter': 0,
                    'heavy_fighter': 0,
                    'cruiser': 0,
                    'battleship': 0,
                    'battlecruiser': 0,
                    'bomber': 0,
                    'destroyer': 0,
                    'deathstar': 0,
                    'small_cargo': 0,
                    'large_cargo': 0,
                    'colony_ship': 0,
                    'recycler': 0,
                    'espionage_probe': 0,
                    'solar_satellite': 0,
                }
            }
            for i in range(1, len(trs)-5):
                name = trs[i].findAll('td')[0].text.strip(' \r\t\n:')
                short_name = ''.join(name.split())
                code = get_code(short_name)
                qty = parse_int(trs[i].findAll('td')[1].text.strip())
                if code == 202: fleet['ships']['small_cargo']     = qty
                if code == 203: fleet['ships']['large_cargo']     = qty
                if code == 204: fleet['ships']['light_fighter']   = qty
                if code == 205: fleet['ships']['heavy_fighter']   = qty
                if code == 206: fleet['ships']['cruiser']         = qty
                if code == 207: fleet['ships']['battleship']      = qty
                if code == 208: fleet['ships']['colony_ship']     = qty
                if code == 209: fleet['ships']['recycler']        = qty
                if code == 210: fleet['ships']['espionage_probe'] = qty
                if code == 211: fleet['ships']['bomber']          = qty
                if code == 212: fleet['ships']['solar_satellite'] = qty
                if code == 213: fleet['ships']['destroyer']       = qty
                if code == 214: fleet['ships']['deathstar']       = qty
                if code == 215: fleet['ships']['battlecruiser']   = qty
            fleets.append(fleet)
        return fleets


    def get_fleet_ids(self):
        """Return the reversable fleet ids."""
        res = self.session.get(self.get_url('movement')).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        spans = soup.findAll('span', {'class': 'reversal'})
        fleet_ids = [span.get('ref') for span in spans]
        return fleet_ids

    def get_attacks(self, checkSpyAlso=False):
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        res = self.session.get(self.get_url('eventList'), params={'ajax': 1},
                               headers=headers).content
        soup = BeautifulSoup(res, 'html.parser')
        if soup.find('head'):
            raise NOT_LOGGED
        events = soup.findAll('tr', {'class': 'eventFleet'})
        eventsDup = filter(lambda x: 'partnerInfo' not in x.get('class', []), events)
        events = soup.findAll('tr', {'class': 'allianceAttack'})
        events += eventsDup
        # unsupported operand type(s) for +=: 'filter' and 'ResultSet'
        attacks = []
        for event in events:
            mission_type = int(event['data-mission-type'])

            if mission_type not in [1, 2, 9, 6]:
                continue

            if mission_type not in [1, 2]:
                if checkSpyAlso and mission_type not in [6]:
                    continue
                elif checkSpyAlso is False:
                    continue
                else:
                    None

            attack = {}
            attack.update({'mission_type': mission_type})
            if mission_type == 1:
                coords_origin = event.find('td', {'class': 'coordsOrigin'}) \
                    .text.strip()
                coords = re.search(r'\[(\d+):(\d+):(\d+)\]', coords_origin)
                galaxy, system, position = coords.groups()
                attack.update({'origin': (int(galaxy), int(system), int(position))})

                # CHECK IF IT IS HOSTILE
                is_hostile = False
                check_hostile = event.find('td', {'class': 'hostile'})
                if check_hostile is not None:
                    is_hostile = True
                attack.update({'is_hostile': is_hostile})
            elif mission_type == 6:
                total_fleet = event.find('td', {'class': 'icon_movement'})
                if total_fleet is None:
                    None
                else:
                    total_fleet = total_fleet.find('span')['title']
                    soup_fleet = BeautifulSoup(total_fleet, 'lxml')
                    total_tr = len(soup_fleet.findAll('tr'))
                    if total_tr > 2:
                        attack.update({'origin': None})
                        attack.update({'is_hostile': True})
                    else:
                        attack.update({'origin': None})
                        attack.update({'is_hostile': False})
            elif mission_type == 9:
                attack.update({'origin': None})
                attack.update({'is_hostile': True})
            else:
                attack.update({'origin': None})
                attack.update({'is_hostile': True})

            dest_moon = event.find('td', {'class': 'destFleet'}).find('figure',
                                                                      {'class': 'planetIcon moon'}) is not None

            attack.update(({'is_toMoon': dest_moon}))
            dest_coords = event.find('td', {'class': 'destCoords'}).text.strip()
            coords = re.search(r'\[(\d+):(\d+):(\d+)\]', dest_coords)
            galaxy, system, position = coords.groups()
            attack.update({'destination': (int(galaxy), int(system), int(position))})

            arrival_time = event.find('td', {'class': 'arrivalTime'}).text.strip()
            coords = re.search(r'(\d+):(\d+):(\d+)', arrival_time)
            hour, minute, second = coords.groups()
            hour = int(hour)
            minute = int(minute)
            second = int(second)
            arrival_time = self.get_datetime_from_time(hour, minute, second)
            attack.update({'arrival_time': arrival_time})

            # todo Mn replace 제대로
            # attack.update({'detailsFleet': int(event.find('td', {'class': 'detailsFleet'}).text.replace(".","").replace("Mn","").strip())})
            try:
                attack.update({'detailsFleet': int(event.find('td', {'class': 'detailsFleet'}).text.strip())})
            except ValueError as ve:
                attack.update({'detailsFleet': "(?)"})

            if mission_type == 1:
                attacker_id = event.find('a', {'class': 'sendMail'})['data-playerid']
                attack.update({'attacker_id': int(attacker_id)})
            else:
                attack.update({'attacker_id': None})

            attacks.append(attack)
        return attacks

    def get_datetime_from_time(self, hour, minute, second):
        attack_time = arrow.utcnow().to(self.server_tz).replace(hour=hour, minute=minute, second=second)
        now = arrow.utcnow().to(self.server_tz)
        if now.hour > attack_time.hour:
            attack_time += datetime.timedelta(days=1)
        return attack_time.to(tz.tzlocal()).datetime

    def get_url(self, page, params=None):
        if params is None:
            params = {}
        if page == 'login':
            return 'https://{}/main/login'.format(self.domain)
        else:
            if self.server_url == '':
                self.server_url = self.get_universe_url(self.universe)
            url = 'https://{}/game/index.php?page={}'.format(self.server_url, page)
            if params:
                arr = []
                for key in params:
                    arr.append("{}={}".format(key, params[key]))
                url += '&' + '&'.join(arr)
            return url

    def get_servers(self, domain):
        res = self.session.get('https://lobby.ogame.gameforge.com/api/servers').json()
        servers = {}
        for server in res:
            name = server['name'].lower()
            lang = server['language']
            num = server['number']
            url = 's{}-{}.ogame.gameforge.com'.format(num, lang)
            servers[name] = url
        return servers

    def get_universe_url(self, universe):
        """Get a universe name and return the server url."""
        servers = self.get_servers(self.domain)
        universe = universe.lower()
        if universe not in servers:
            raise BAD_UNIVERSE_NAME
        return servers[universe]

    def get_server_time(self):
        """Get the ogame server time."""
        res = self.session.get(self.get_url('overview')).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        date_str = soup.find('li', {'class': 'OGameClock'}).text
        date_format = '%d.%m.%Y %H:%M:%S'
        date = datetime.datetime.strptime(date_str, date_format)
        return date

    def get_planet_infos(self, planet_id, res=None):
        if not res:
            res = self.session.get(self.get_url('overview', {'cp': planet_id})).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        link = soup.find('div', {'id': 'planet-{}'.format(planet_id)})
        if  link is not None: #is a planet pid
            link = link.find('a')
        else:  # is a moon pid
            link = soup.find('div', {'id': 'planetList'})
            link = link.find_all('a', {'class': 'moonlink'})
            for node in link:
                nodeContent = node['title']
                if nodeContent.find("cp=" + planet_id) > -1:
                    link = node
                    break
                else:
                    continue


        infos_label = BeautifulSoup(link['title'], 'html.parser').text
        infos = get_planet_infos_regex(infos_label)
        isUnderConstruction = soup.find('table', {'class': "construction active"}).find('td', {'class': "desc timer"})
        if isUnderConstruction is None:
            isUnderConstruction = False
        else:
            isUnderConstruction = True

        res = {}
        res['img'] = link.find('img').get('src')
        res['id'] = planet_id
        res['planet_name'] = infos.group(1)
        res['coordinate'] = {}
        res['coordinate']['galaxy'] = int(infos.group(2))
        res['coordinate']['system'] = int(infos.group(3))
        res['coordinate']['position'] = int(infos.group(4))
        res['diameter'] = parse_int(infos.group(5))
        res['fields'] = {}
        res['fields']['built'] = int(infos.group(6))
        res['fields']['total'] = int(infos.group(7))
        res['temperature'] = {}
        res['isUnderConstruction'] = isUnderConstruction
        if infos.groups().__len__() > 7:  # is a planet
            res['temperature']['min'] = int(infos.group(8))
            res['temperature']['max'] = int(infos.group(9))
        return res

    def get_ogame_version(self, res=None):
        """Get ogame version on your server."""
        if not res:
            res = self.session.get(self.get_url('overview')).content
        if not self.is_logged(res):
            raise NOT_LOGGED
        soup = BeautifulSoup(res, 'html.parser')
        footer = soup.find('div', {'id': 'siteFooter'})
        version = footer.find('a').text.strip()
        return version

    def get_overview(self, planet_id):
        html = self.session.get(self.get_url('overview', {'cp': planet_id})).content
        update_cookies(self.session.cookies)
        if not self.is_logged(html):
            raise NOT_LOGGED
        soup = BeautifulSoup(html, 'html.parser')
        boxes = soup.findAll('div', {'class': 'content-box-s'})
        res = {}
        names = ['buildings', 'research', 'shipyard']
        for idx, box in enumerate(boxes):
            is_idle = box.find('td', {'class': 'idle'}) is not None
            res[names[idx]] = []
            if not is_idle:
                name = box.find('th').text.encode('utf8')
                short_name = ''.join(name.split())
                code = get_code(short_name)
                desc = box.find('td', {'class': 'desc'}).text
                desc = ' '.join(desc.split())
                tmp = [{'name': short_name, 'code': code}]
                if idx == 2:
                    quantity = parse_int(box.find('div', {'id': 'shipSumCount7'}).text)
                    tmp[0].update({'quantity': quantity})
                    queue = box.find('table', {'class': 'queue'})
                    if queue:
                        tds = queue.findAll('td')
                        for td_element in tds:
                            link = td_element.find('a')
                            quantity = parse_int(link.text)
                            img = td_element.find('img')
                            alt = img['alt']
                            short_name = unicode(''.join(alt.split())).encode('utf-8')
                            code = get_code(short_name)
                            tmp.append({'name': short_name, 'code': code, 'quantity': quantity})
                res[names[idx]] = tmp
        return res

    def get_resource_settings(self, planet_id):
        html = self.session.get(self.get_url('resourceSettings', {'cp': planet_id})).content
        if not self.is_logged(html):
            raise NOT_LOGGED
        soup = BeautifulSoup(html, 'html.parser')
        options = soup.find_all('option', {'selected': True})
        res = {}
        res['metal_mine'] = options[0]['value']
        res['crystal_mine'] = options[1]['value']
        res['deuterium_synthesizer'] = options[2]['value']
        res['solar_plant'] = options[3]['value']
        res['fusion_reactor'] = options[4]['value']
        res['solar_satellite'] = options[5]['value']
        return res

    def send_message(self, player_id, msg):
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        payload = {'playerId': player_id,
                   'text': msg,
                   'mode': 1,
                   'ajax': 1}
        url = self.get_url('ajaxChat')
        self.session.post(url, data=payload, headers=headers)

    def galaxy_content(self, galaxy, system):
        headers = {'X-Requested-With': 'XMLHttpRequest',
                   'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}
        payload = {'galaxy': galaxy, 'system': system}
        url = self.get_url('galaxyContent', {'ajax': 1})
        res = self.session.post(url, data=payload, headers=headers).content.decode('utf8')
        try:
            obj = json.loads(res)
        except ValueError:
            raise NOT_LOGGED
        return obj

    def find_empty_slots(self, html):
        soup = BeautifulSoup(html, 'lxml')
        empty_rows = soup.find_all('tr', {'class': 'empty_filter'})
        empty_positions = []
        if empty_rows is None:
            return empty_positions
        for empty_row in empty_rows:
            empty_positions.append(empty_row.find('td', {'position'}).text)

        return empty_positions

    def get_spy_reports(self):
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        payload = {'tab': 20,
                   'ajax': 1}
        url = self.get_url('messages', payload)
        res = self.session.get(url).content.decode('utf8')
        return res

    def delete_spy_reports(self, message_id):
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        payload = {'messageId': message_id, 'action': 103, 'ajax': 1}
        url = self.get_url('messages')
        res = self.session.post(url, data=payload, headers=headers).content.decode('utf8')

        return res
        
    def send_spy(self, galaxy, system, position, ship_count):
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        payload = {'mission': 6,
                   'type': 1,
                   'token': '',
                   'galaxy': galaxy,
                   'system': system,
                   'position': position,
                   'shipCount': ship_count,
                   'speed': 10}

        token = ''
        if miniFleetToken is None or miniFleetToken == '':
            first_res = self.session.get(self.get_url('overview')).content
            moon_soup = BeautifulSoup(first_res, 'html.parser')
            data = moon_soup.find_all('script', {'type': 'text/javascript'})
            parameter = 'miniFleetToken'
            for d in data:
                d = d.text
                if 'var miniFleetToken=' in d:
                    regex_string = 'var {parameter}="(.*?)"'.format(parameter=parameter)
                    token = re.findall(regex_string, d)
        else:
            token = miniFleetToken

        url = self.get_url('minifleet', {'ajax': 1})
        payload['token'] = token
        res = self.session.post(url, data=payload, headers=headers).content.decode('utf8')
        return res

    def get_flying_fleets(self):
        url = self.get_url('movement')
        res = self.session.get(url).content
        soup = BeautifulSoup(res, 'lxml')
        current_fleets = soup.find('span', {
            'class': 'current'})
        max_fleets = soup.find('span', {
            'class': 'all'})
        if current_fleets is None:
            text_fleets = soup.find('span', {'class': 'tooltip advice'}).contents[1]
            current_fleets = int(text_fleets.split('/')[0])
            max_fleets = int(text_fleets.split('/')[1])
            available_fleets = max_fleets - current_fleets
            fleet_dict = {'current_fleets': current_fleets, 'max_fleets': max_fleets,
                          'available_fleets': available_fleets}
            return fleet_dict
        current_fleets = int(current_fleets.contents[0])
        max_fleets = int(max_fleets.contents[0])
        available_fleets = max_fleets - current_fleets
        fleet_dict = {'current_fleets': current_fleets, 'max_fleets': max_fleets, 'available_fleets': available_fleets}
        return fleet_dict

    def jumpgate_execute(self):
        res = self.session.get(self.get_url('jumpgate_execute')).content
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        return True

    def send_minifleet_spy(self, where, ship_count, token):
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        payload = {'mission': 6,
                   'galaxy': where.get('galaxy'),
                   'system': where.get('system'),
                   'position': where.get('position'),
                   'type': 1,
                   'shipCount': ship_count,
                   'token': token,
                   'speed': 10
                   }
        res = self.session.post(self.get_url('minifleet'), params={'ajax': 1}, headers=headers, data=payload).content
        try:
            json_response = json.loads(res)
        except ValueError:
            # from send_message import send_message
            # send_message(
            #     'No se pudo espiar a {}:{}:{}'.format(where.get('galaxy'), where.get('system'), where.get('position')))
            return None
        res_dict = json_response.get('response').get('success')
        if res_dict:
            return 'Sended spy probes'
        return None

    def get_minifleet_token(self, html):
        token = None
        moon_soup = BeautifulSoup(html, 'html.parser')
        data = moon_soup.find_all('script', {'type': 'text/javascript'})
        parameter = 'miniFleetToken'
        for d in data:
            d = d.text
            if 'var miniFleetToken=' in d:
                regex_string = 'var {parameter}="(.*?)"'.format(parameter=parameter)
                token = re.findall(regex_string, d)

        return token

    def check_new_messages(self, html):
        total_messages = 0
        soup = BeautifulSoup(html, 'lxml')
        messages = soup.find('span', {'class': 'totalChatMessages'})
        if messages is not None:
            total_messages = int(messages.attrs['data-new-messages'])

        return total_messages

    def get_new_messages(self):
        msg_list = []
        html = self.session.get(self.get_url('chat')).content
        soup = BeautifulSoup(html, 'lxml')
        new_chats = soup.find('ul', {'id': 'chatMsgList'}).findAll('li', {'class': 'msg_new'})
        if new_chats is None:
            return None
        for chat in new_chats:
            message = ''
            player = ''
            try:
                message = chat.find('span', {'class': 'msg_content'}).contents[0]
                player = chat.find('span', {'class': 'msg_title'}).contents[2]
            except UnicodeEncodeError:
                print('Error getting messages')
            msg = {'message': message.encode('utf-8'), 'player': player.encode('utf-8')}
            msg_list.append(msg)

        return msg_list

    def can_build(self, planet_id, building, building_type):
        building_url = building_type
        if building_type == 'supply':
            building_url = 'resources'

        html = self.session.get(self.get_url(building_url, {'cp': planet_id})).content
        soup = BeautifulSoup(html, 'lxml')
        item_div = soup.find('div', {'class': '{}{}'.format(building_type, building)})
        is_free = item_div.find('a', {'class': 'fastBuild'})
        if is_free is not None:
            return True
        else:
            return False

    def can_build_defenses(self, planet_id, defense_item):
        html = self.session.get(self.get_url('defense', {'cp': planet_id})).content
        defense_code = constants.Defense[defense_item]
        soup = BeautifulSoup(html, 'lxml')
        in_construction = soup.find('div', {'class': 'defense{}'.format(defense_code)}).find('div',
                                                                                             {'class': 'construction'})
        parent_class = soup.find('div', {'class': 'defense{}'.format(defense_code)}).parent.attrs['class']
        if in_construction is not None or parent_class == 'off':
            return False
        else:
            return True

    def can_build_ships(self, planet_id, ship_item):
        html = self.session.get(self.get_url('shipyard', {'cp': planet_id})).content
        ship_code = constants.Ships[ship_item]
        soup = BeautifulSoup(html, 'lxml')
        ship_type = 'civil'

        # get the type of ship
        if ship_code in [204, 205, 206, 207, 213, 214, 215, 211]:
            ship_type = 'military'

        in_construction = soup.find('div', {'class': '{}{}'.format(ship_type, ship_code)}).find('div', {
            'class': 'construction'})
        parent_class = soup.find('div', {'class': '{}{}'.format(ship_type, ship_code)}).parent.attrs['class']
        if in_construction is not None or parent_class == 'off':
            return False
        else:
            return True

    def can_build_research(self, building):
        html = self.session.get(self.get_url('research')).content
        soup = BeautifulSoup(html, 'lxml')
        is_free = soup.find('div', {'class': 'research{}'.format(building)}).find('a', {'class': 'fastBuild'})
        if is_free is not None:
            return True
        else:
            return False

    def alliance_apply(self, alliance_id, message):
        url = self.get_url('allianceWriteApplication', {'action': 1})
        payload = {'text': message,
                   'appliedAllyId': alliance_id}
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        res = self.session.post(url, headers=headers, data=payload).content

    def get_ip(self):
        res = self.session.get('http://ifconfig.me/ip')
        return 'ip session: {}'.format(res.text.strip())

    def delete_planet(self, planet_id):
        html = self.session.get(self.get_url('planetlayer')).content
        soup = BeautifulSoup(html, 'lxml')
        form = soup.find('form', {'id': 'planetMaintenanceDelete'})
        abandon = form.find('input', {'name': 'abandon'}).get('value')
        token = form.find('input', {'name': 'token'}).get('value')
        payload = {'abandon': abandon,
                   'token': token,
                   'password': self.password}
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        check_password = self.session.post(self.get_url('checkPassword'), headers=headers, data=payload).content
        jo = json.loads(check_password)
        new_token = jo['newToken']
        delete_payload = {'abandon': abandon,
                          'token': new_token,
                          'password': self.password}

        delete_action = self.session.post(self.get_url('planetGiveup'), headers=headers, data=delete_payload).content

    def Consommation(self, type, batiment, lvl):

        """ Retourne la consommation du batiment du level lvl + 1 """
        energieLvl = constants.Formules[type][batiment]['consommation'][0] * lvl * (
            constants.Formules[type][batiment]['consommation'][1] ** lvl)
        energieNextLvl = constants.Formules[type][batiment]['consommation'][0] * (lvl + 1) * (
            constants.Formules[type][batiment]['consommation'][1] ** (lvl + 1))
        return math.floor(energieNextLvl - energieLvl)

    def building_cost(self, type, batiment, lvl):
        """ Retourne le cout d'un batiment lvl + 1 """
        cost = {}
        cost['metal'] = int(math.floor(constants.Formules[type][batiment]['cout']['Metal'][0] *
                                       constants.Formules[type][batiment]['cout']['Metal'][1] ** (lvl - 1)))
        cost['crystal'] = int(math.floor(constants.Formules[type][batiment]['cout']['Crystal'][0] *
                                         constants.Formules[type][batiment]['cout']['Crystal'][1] ** (lvl - 1)))
        cost['deuterium'] = int(math.floor(constants.Formules[type][batiment]['cout']['Deuterium'][0] *
                                           constants.Formules[type][batiment]['cout']['Deuterium'][1] ** (lvl - 1)))
        return cost

    def building_time(self, cost, robotics, nano, speed):
        costSum = cost['metal'] + cost['crystal']
        buildTime = (costSum) / (2500 * (1 + robotics
                                         * (2 ** nano) * speed))
        # buildCost = self.OGame.building_cost('Storage', 'metal_storage', building['metal_storage'] + 1)
        # Time hours = Metal + crystal /   (  2500 * ( 1+ robotics * 2^nano * serverspeed )
        return buildTime

    def getProduction(self, type, batiment, lvl):
        """ Retourne le cout d'un batiment lvl + 1 """
        production = 0
        production = (self.universe_speed * constants.Formules[type][batiment]['production'][0] * lvl *
                      (constants.Formules[type][batiment]['production'][1] ** lvl) ) + \
                     self.universe_speed * constants.Formules[type][batiment]['production'][0]


        return production


    def storageSize(self, type, batiment, lvl):
        capacity = -1
        capacity = 5000 * int(math.floor(2.5 * (math.e ** (lvl * 20 / 33))))
        return capacity

    def galaxy_infos(self, galaxy, system):
        html = self.galaxy_content(galaxy, system)['galaxy']
        soup = BeautifulSoup(html, 'html.parser')
        rows = soup.findAll('tr', {'class': 'row'})
        res = []
        for row in rows:
            if 'empty_filter' not in row.get('class'):
                activity = None
                activity_div = row.findAll('div', {'class': 'activity'})
                if len(activity_div) > 0:
                    activity_raw = activity_div[0].text.strip()
                    if activity_raw != '':
                        activity = int(activity_raw)
                    else:
                        activity = 0
                tooltips = row.findAll('div', {'class': 'htmlTooltip'})
                planet_tooltip = tooltips[0]
                planet_name = planet_tooltip.find('h1').find('span').text
                planet_url = planet_tooltip.find('img').get('src')
                coords_raw = planet_tooltip.find('span', {'id': 'pos-planet'}).text
                coords = re.search(r'\[(\d+):(\d+):(\d+)\]', coords_raw)
                galaxy, system, position = coords.groups()
                planet_infos = {}
                planet_infos['activity'] = activity
                planet_infos['name'] = planet_name
                planet_infos['img'] = planet_url
                planet_infos['coordinate'] = {}
                planet_infos['coordinate']['galaxy'] = int(galaxy)
                planet_infos['coordinate']['system'] = int(system)
                planet_infos['coordinate']['position'] = int(position)
                if len(tooltips) > 2:
                    for i in range(1, 3):
                        player_tooltip = tooltips[i]
                        player_id_raw = player_tooltip.get('id')
                        if player_id_raw.startswith('debris'):
                            continue
                        player_id = int(re.search(r'player(\d+)', player_id_raw).groups()[0])
                        player_name = player_tooltip.find('h1').find('span').text
                        player_rank = parse_int(player_tooltip.find('li', {'class': 'rank'}).find('a').text)
                        break
                elif len(tooltips) > 1:
                    player_tooltip = tooltips[1]
                    player_id_raw = player_tooltip.get('id')
                    if "player" not in player_id_raw:
                        continue
                    player_id = int(re.search(r'player(\d+)', player_id_raw).groups()[0])
                    player_name = player_tooltip.find('h1').find('span').text
                    player_rank = parse_int(player_tooltip.find('li', {'class': 'rank'}).find('a').text)
                else:
                    player_id = None
                    player_name = row.find('td', {'class': 'playername'}).find('span').text.strip()
                    player_rank = None
                planet_infos['player'] = {}
                planet_infos['player']['id'] = player_id
                planet_infos['player']['name'] = player_name
                planet_infos['player']['rank'] = player_rank
                res.append(planet_infos)
        return res

    def change_player_name(self, new_name):
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        modal_content = self.session.get(self.get_url('changenick'), headers=headers).content
        payload = {'nick': new_name,
                   'pass': self.password,
                   'ajax': 1}
        self.session.post(self.get_url('changenick'), headers=headers, data=payload)

    def create_alliance(self, alliance_name, alliance_tag):
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        url = self.get_url('allianceCreation', {'action': 16})
        payload = {'allyTag': alliance_tag,
                   'allyName': alliance_name,
                   'token': 'undefined'}
        html = self.session.post(url, headers=headers, data=payload).content
        soup = BeautifulSoup(html, 'lxml')
        a_name = soup.find('meta', {'name': 'ogame-alliance-name'})
        a_tag = soup.find('meta', {'name': 'ogame-alliance-tag'})

        alliance_data = {'alliance_name': '', 'alliance_tag': ''}

        if alliance_name is not None and alliance_tag is not None:
            alliance_data['alliance_name'] = alliance_name

        return alliance_data

    def accept_alliance_requests(self):
        payload = {'ajax': 1}
        headers = {'X-Requested-With': 'XMLHttpRequest'}
        html = self.session.post(self.get_url('allianceApplications'), headers=headers, data=payload).content
        soup = BeautifulSoup(html, 'lxml')
        actions = soup.find_all('a', {'class': 'action', 'rel': '3'})
        for idx, action in enumerate(actions):
            rel = action.attrs['rel']
            if idx % 2 == 0:
                form_value = action.attrs['rev'][0]
                form = form_value.replace('form_', '')
                token = action.attrs['token']
                url = self.get_url('allianceApplications',
                                   {'action': 3, 'applicationId': form, 'token': token, 'text': ''})
                content = self.session.post(url, headers=headers)

        return []

    def get_player_metas(self):
        res = self.session.get(self.get_url('overview')).content
        soup = BeautifulSoup(res, 'lxml')
        player_id = soup.find('meta', {'name': 'ogame-player-id'})['content']
        player_name = soup.find('meta', {'name': 'ogame-player-name'})['content']
        alliance_id = soup.find('meta', {'name': 'ogame-alliance-id'})['content']
        alliance_name = soup.find('meta', {'name': 'ogame-alliance-name'})['content']
        alliance_tag = soup.find('meta', {'name': 'ogame-alliance-tag'})['content']

        data = {'player_id': player_id, 'player_name': player_name, 'alliance_id': alliance_id,
                'alliance_tag': alliance_tag, 'alliance_name': alliance_name}

        return data

    def get_max_colonies(self):
        astro_level = self.get_research()['astrophysics']
        if astro_level % 2 == 1:
            astro_level = astro_level + 1

        return astro_level / 2

    def can_colonize_more_planets(self):
        planet_list = self.get_planet_ids()
        max_colonies = self.get_max_colonies()

        if len(planet_list) < max_colonies:
            return True
        else:
            return False
