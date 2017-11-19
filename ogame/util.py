from xml.etree import ElementTree as ET
import os
import random


def get_random_user_agent():
    folder = os.path.dirname(os.path.abspath(__file__))
    xml_file = open(os.path.join(folder, 'user_agents.xml'), 'r')
    tree = ET.parse(xml_file)
    root = tree.getroot()

    user_agents = []

    for child in root:
        at = child.attrib
        user_agents.append(at.get('useragent'))

    secure_random = random.SystemRandom()
    return secure_random.choice(user_agents)
