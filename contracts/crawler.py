from datetime import datetime
import os
import pickle
import re
import mechanize as mc
import cookielib
import json

import models


def clean_price(item):
    price = item[u'initialContractualPrice']
    price = price.split(' ')[0]
    price = price.replace(".", "").replace(",", "")
    return int(price)


def clean_contracted(item):
    return models.Entity.objects.filter(base_id__in=[contracted['id'] for contracted in item['contracted']])


def clean_contractors(item):
    return models.Entity.objects.filter(base_id__in=[contractor['id'] for contractor in item['contracting']])


def clean_date(string_date):
    if string_date:
        return datetime.strptime(string_date, "%d-%m-%Y").date()
    else:
        return None


def clean_cpvs(item):
    """
    It is like {u'cpvs': u'79822500-7, Servi\xe7os de concep\xe7\xe3o gr\xe1fica'}
    we want '79822500-7'
    """
    return item[u'cpvs'].split(',')[0]


def clean_contract_type(item):
    try:
        return models.ContractType.objects.get(name=item[u'contractTypes'])
    except models.ContractType.DoesNotExist:
        return None


def clean_procedure_type(item):
    try:
        return models.ProcedureType.objects.get(name=item[u'contractingProcedureType'])
    except models.ProcedureType.DoesNotExist:
        return None


def clean_place(item, data):
    """
    It is like {u'executionPlace': u'Portugal, Faro, Castro Marim'}
    but it can come without council or even district.
    """
    data['country'] = None
    data['district'] = None
    data['council'] = None
    if 'executionPlace' in item and item['executionPlace']:
        names = re.split(', |<BR/>', item['executionPlace']) # we only take the first place (they can be more than one)
        if len(names) >= 1:
            country_name = names[0]
            data['country'] = models.Country.objects.get(name=country_name)
            if len(names) >= 2:
                district_name = names[1]
                try:
                    data['district'] = models.District.objects.get(name=district_name, country__name=country_name)
                except models.District.DoesNotExist:
                    return data
                if len(names) >= 3:
                    council_name = names[2]
                    try:
                        data['council'] = models.Council.objects.get(name=council_name, district__name=district_name)
                    except models.Council.DoesNotExist:
                        return data
    return data


def clean_country(item):
    try:
        country = models.Country.objects.get(name=item['country'])
    except IndexError:
        country = None
    except models.Country.DoesNotExist:
        country = None

    return country


class Crawler():
    data_directory = '../../data'
    contracts_directory = '../../contracts'

    class NoMoreEntriesError:
        pass

    def __init__(self):
        # Browser
        br = mc.Browser()

        # Cookie Jar
        cj = cookielib.LWPCookieJar()
        br.set_cookiejar(cj)

        # Browser options
        br.set_handle_equiv(True)
        br.set_handle_redirect(True)
        br.set_handle_referer(True)
        br.set_handle_robots(False)

        # Follows refresh 0 but not hangs on refresh > 0
        br.set_handle_refresh(mc._http.HTTPRefreshProcessor(), max_time=1)

        # Want debugging messages?
        #br.set_debug_http(True)
        #br.set_debug_redirects(True)
        #br.set_debug_responses(True)

        # User-Agent. For choosing one, use for instance this with your browser: http://whatsmyuseragent.com/
        br.addheaders = [('User-agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_5) '
                                        'AppleWebKit/537.36 (KHTML, like Gecko)'),
                         ('Range', "items=0-24")]
        self.browser = br

    def goToPage(self, url):
        response = self.browser.open(url)
        html = response.read()

        return json.loads(html)

    def block_to_range(self, i):
        return i*25, (i+1)*25 - 1

    def _get_entities_block(self, block):
        """
        Returns a block of 25 entities from a file.
        If the file doesn't exist or returns less than 25 entries,
        it hits BASE's database and updates the file.

        It raises a `NoMoreEntriesError` if the retrieval returned 0 entries i.e. if have we reached
        the last existing entity in Base's database.
        """

        def _retrieve_entities(block):
            print '_retrieve_entities(%d)' % block
            self.browser.addheaders[1] = ('Range', "items=%d-%d" % self.block_to_range(block))
            data = self.goToPage("http://www.base.gov.pt/base2/rest/entidades")
            if len(data) == 0:
                raise self.NoMoreEntriesError
            return data

        file_name = '%s/%d_entities.dat' % (self.data_directory, block)
        try:
            f = open(file_name, "rb")
            data = pickle.load(f)
            if len(data) != 25:  # if block is not complete, we retrieve it again to try to complete it.
                # todo, write another error for this case
                print '_get_entities_block(%d)' % block,
                print 'returns len(data) = %d != 25' % len(data)
                raise IOError
            f.close()
        except IOError:
            data = _retrieve_entities(block)
            f = open(file_name, "wb")
            pickle.dump(data, f)
            f.close()
        return data

    def _last_entity_block(self):
        """
        Returns the last block we were able to retrieve from the database.
        This is computed using the files we saved. The regex expression must be compatible to the
        name given in `_get_entities_block`.
        """
        regex = re.compile(r"(\d+)_entities.dat")
        files = [int(re.findall(regex, f)[0]) for f in os.listdir('%s/' % self.data_directory) if re.match(regex, f)]
        files = sorted(files, key=lambda x: int(x), reverse=True)
        return files[0]

    def _last_contract_block(self):
        """
        Returns the last block existent in the database
        This is computed using the files we saved. The regex expression must be compatible to the
        name given in `_get_contracts_block`.
        """
        regex = re.compile(r"(\d+).dat")
        files = [int(re.findall(regex, f)[0]) for f in os.listdir('%s/' % self.data_directory) if re.match(regex, f)]
        files = sorted(files, key=lambda x: int(x), reverse=True)
        return files[0]

    def _get_contracts_block(self, block):
        """
        Returns a block of 25 contracts from a file.
        If the file doesn't exist or returns less than 25 contracts,
        it hits Base's database and updates the file.

        It raises a `NoMoreEntriesError` if the retrieval returned 0 contracts i.e. if we have reached
        the last existing contract in Base's database.
        """
        def _retrieve_contracts(block):
            self.browser.addheaders[1] = ('Range', "items=%d-%d" % self.block_to_range(block))
            data = self.goToPage("http://www.base.gov.pt/base2/rest/contratos")
            if len(data) == 0:  # if there are no entries, we just stop the procedure.
                raise self.NoMoreEntriesError
            return data

        file_name = '%s/%d.dat' % (self.data_directory, block)
        try:
            f = open(file_name, "rb")
            data = pickle.load(f)
            if len(data) != 25:  # if block is not complete, we retrieve it again to complete it.
                # todo, write another error for this case
                print '_get_contracts_block(%d)' % block,
                print 'returns len(data) = %d != 25' % len(data)
                raise IOError
            f.close()
        except IOError:
            # online retrieval
            data = _retrieve_contracts(block)

            f = open(file_name, "wb")
            pickle.dump(data, f)
            f.close()
        return data

    def _retrieve_contract(self, base_id):
        """
        Retrieves a specific contract.
        """
        print '_retrieve_contract(%d)' % base_id
        url = 'http://www.base.gov.pt/base2/rest/contratos/%d' % base_id
        return self.goToPage(url)

    def _contract(self, base_id):
        """
        Returns the data of a contract from a file. If the file doesn't exist,
        it retrieves the data from Base, saves it, and returns it.
        """
        file_name = '%s/%d.dat' % (self.contracts_directory, base_id)
        try:
            f = open(file_name, "rb")
            data = pickle.load(f)
            f.close()
        except IOError:
            # online retrieval
            data = self._retrieve_contract(base_id)
            f = open(file_name, "wb")
            pickle.dump(data, f)
            f.close()
        return data

    def retrieve_and_save_contracts_types(self):
        url = 'http://www.base.gov.pt/base2/rest/lista/tipocontratos'
        data = self.goToPage(url)

        for element in data['items']:
            if element['id'] == '0':  # id = 0 is "All" that we don't use.
                continue
            try:
                # if it exists, we pass
                models.ContractType.objects.get(base_id=element['id'])
                pass
            except models.ContractType.DoesNotExist:
                contract_type = models.ContractType(name=element['description'], base_id=element['id'])
                contract_type.save()

    def retrieve_and_save_procedures_types(self):
        url = 'http://www.base.gov.pt/base2/rest/lista/tipoprocedimentos'
        data = self.goToPage(url)

        for element in data['items']:
            if element['id'] == '0':  # id = 0 is "All that we don't use.
                continue
            try:
                # if it exists, we pass
                models.ProcedureType.objects.get(name=element['description'])
                pass
            except models.ProcedureType.DoesNotExist:
                procedure_type = models.ProcedureType(name=element['description'], base_id=element['id'])
                procedure_type.save()

    def retrieve_and_save_countries(self):
        url = 'http://www.base.gov.pt/base2/rest/lista/paises'
        data = self.goToPage(url)

        for element in data['items']:
            try:
                # if it exists, we pass
                models.Country.objects.get(name=element['description'])
                pass
            except models.Country.DoesNotExist:
                country = models.Country(name=element['description'])
                country.save()

    def retrieve_and_save_districts(self):
        url = 'http://www.base.gov.pt/base2/rest/lista/distritos?pais=187'  # 187 is Portugal.
        data = self.goToPage(url)

        portugal = models.Country.objects.get(name="Portugal")

        for element in data['items']:
            if element['id'] == '0':  # id = 0 is "All" that we don't use.
                continue
            try:
                # if it exists, we pass
                models.District.objects.get(base_id=element['id'])
                pass
            except models.District.DoesNotExist:
                district = models.District(name=element['description'], base_id=element['id'], country=portugal)
                district.save()

    def retrieve_and_save_councils(self):

        def save_council(district):
            url = 'http://www.base.gov.pt/base2/rest/lista/concelhos?distrito=%d' % district.base_id
            data = self.goToPage(url)

            for element in data['items']:
                if element['id'] == '0':  # id = 0 is "All", that we don't use.
                    continue
                try:
                    # if it exists, we pass
                    models.Council.objects.get(base_id=element['id'])
                    pass
                except models.Council.DoesNotExist:
                    council = models.Council(name=element['description'], base_id=element['id'], district=district)
                    council.save()

        for district in models.District.objects.all():
            save_council(district)

    def _save_entities(self, block):
        """
        Saves a set of 25 entities, identified by a block, into the database.
        If an entity already exists, it updates its data.
        """
        print '_save_entities(%d)' % block
        for data in self._get_entities_block(block):
            country = clean_country(data)

            # if entity exists, we update its data
            try:
                entity = models.Entity.objects.get(base_id=int(data['id']))
                entity.name = data['description']
                entity.country = country
                entity.nif = data['nif']
                entity.save()
            # else, we create it with the data
            except models.Entity.DoesNotExist:
                models.Entity.objects.create(name=data['description'],
                                             base_id=int(data['id']),
                                             country=country,
                                             nif=data['nif'])

    def update_entities(self):
        """
        Saves Goes to all blocks,
        """
        block = self._last_entity_block()
        while True:
            try:
                self._save_entities(block)
                block += 1
            except self.NoMoreEntriesError:
                break

    def _save_contract(self, item):
        print '_save_contract(%s):' % item['id'],
        data = {'base_id': item['id'],
                'procedure_type': clean_procedure_type(item),
                'contract_type': clean_contract_type(item),
                'contract_description': item['objectBriefDescription'],
                'description': item['description'],
                'signing_date': clean_date(item['signingDate']),
                'added_date': clean_date(item['publicationDate']),
                'cpvs': clean_cpvs(item),
                'price': clean_price(item)}
        data = clean_place(item, data)

        # we try to associate the cpvs to a category
        try:
            data['category'] = models.Category.objects.get(code=data['cpvs'])
        except models.Category.DoesNotExist:
            data['category'] = None
        try:
            # we try to get the contract
            contract = models.Contract.objects.get(base_id=data['base_id'])
            print 'contract %d already exists' % data['base_id']
        except models.Contract.DoesNotExist:
            # if it doesn't exist, we create it
            contract = models.Contract.objects.create(**data)
            print 'contract %d saved' % data['base_id']

        contractors = clean_contractors(item)
        contracted = clean_contracted(item)
        contract.contracted.add(*list(contracted))
        contract.contractors.add(*list(contractors))

    def _save_contracts(self, block):
        print 'save_contracts(%d)' % block
        raw_contracts = self._get_contracts_block(block)

        for raw_contract in raw_contracts:
            try:
                data = self._contract(raw_contract['id'])
                self._save_contract(data)
            # this has given errors before, we print the contract number to gain some information.
            except:
                print 'error on saving contract %d' % raw_contract['id']
                raise

    def update_contracts(self):
        block = self._last_contract_block()
        while True:
            try:
                self._save_contracts(block)
                block += 1
            except self.NoMoreEntriesError:
                break

crawler = Crawler()

# from contracts.crawler import crawler; crawler.update_contracts()
