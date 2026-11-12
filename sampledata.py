import json

def samples():
    document = {'openapi':'3.1.0','info':{'title':'Supplier','version':'1'},
        'servers':[{'url':'http://supplier.local'}],
        'paths':{'/items':{'parameters':[{'name':'ids','in':'query','style':'form','explode':False,'schema':{'type':'array','items':{'type':'string'}}}],
                          'get':{'operationId':'items','security':[{'apiKey':[]}], 'responses':{'200':{'description':'ok','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Page'}}}}}},
                          'post':{'operationId':'create','responses':{'201':{'description':'created'}}}}},
        'components':{'securitySchemes':{'apiKey':{'type':'apiKey','in':'header','name':'X-Key'}},
                      'schemas':{'Page':{'type':'object','required':['items'],'properties':{'items':{'type':'array','items':{'type':['string','null']}}}}}}}
    return {'sample.json':json.dumps(document).encode(),
            'invalid.json':b'{"openapi":"2.0","paths":{}}'}
