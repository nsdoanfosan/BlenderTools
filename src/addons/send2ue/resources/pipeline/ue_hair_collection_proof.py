"""Compare full native collection values, with explicit G-probe exclusions."""
from copy import deepcopy
import re

KEY='collection_semantic_v1'
HASH=re.compile(r'^[0-9a-f]{64}$')
ATTRIBUTE_FIELDS=('name','type_id','type_name','count','dependency','persistent','codec','blake3')
def _native_order(value):return value.encode('utf-16-be',errors='surrogatepass')

def semantic(row):
    proof=row.get(KEY)
    if not isinstance(proof,dict) or proof.get('complete') is not True or proof.get('unsupported'):
        raise ValueError('Complete native semantic collection proof is required')
    if proof.get('algorithm')!='blake3-256' or not HASH.fullmatch(str(proof.get('blake3',''))):
        raise ValueError('Invalid native semantic proof algorithm/hash')
    if proof.get('contract')!='managed_array_collection.semantic.v1' or proof.get('includes_uv') is not True:
        raise ValueError('Native semantic proof must include all UV values under the reviewed contract')
    groups=proof.get('groups')
    if not isinstance(groups,list) or not groups:
        raise ValueError('Native semantic group inventory is missing')
    names=[g.get('name') for g in groups]
    if any(not isinstance(n,str) or not n for n in names) or names!=sorted(set(names),key=_native_order):
        raise ValueError('Semantic groups must be uniquely sorted by native name')
    result=[]
    for group in groups:
        count=group.get('count')
        if type(count) is not int or count<0 or not isinstance(group.get('attributes'),list):
            raise ValueError('Invalid semantic group count/attribute inventory')
        attrs=group['attributes'];names=[a.get('name') for a in attrs]
        if any(not isinstance(n,str) or not n for n in names) or names!=sorted(set(names),key=_native_order):
            raise ValueError('Semantic attributes must be uniquely sorted by native name')
        selected=[]
        for attr in attrs:
            if not all(k in attr for k in ATTRIBUTE_FIELDS):
                raise ValueError('Incomplete semantic attribute metadata')
            if attr.get('complete') is not True or attr.get('group_count')!=count:
                raise ValueError('Native attribute value proof is incomplete')
            if (type(attr['type_id']) is not int or attr['count']!=count
                    or type(attr['count']) is not int or type(attr['persistent']) is not bool
                    or not isinstance(attr['dependency'],str) or not attr['type_name'] or not attr['codec']
                    or not HASH.fullmatch(str(attr['blake3']))):
                raise ValueError('Invalid semantic attribute type/count/dependency/value hash')
            selected.append({k:attr[k] for k in ATTRIBUTE_FIELDS})
        result.append({'name':group['name'],'count':count,'attributes':selected})
    return {'algorithm':proof['algorithm'],'blake3':proof['blake3'],'groups':result}

def _view(audit):
    result=deepcopy(audit)
    for row in result['collections']:
        row.pop('non_uv_collection_md5',None)
        row[KEY]=semantic(row)
        for key in ('weight_maps','sim_float_attributes','render_float_attributes'):
            if key in row:
                values=row[key]
                if len({v['name'] for v in values})!=len(values):
                    raise ValueError('Duplicate exposed native attribute name')
                row[key]=sorted(values,key=lambda v:v['name'])
    return result

def assert_equal(before,after):
    """Raw serialization differences require complete, equal full-value proofs."""
    rows=list(before.get('collections',[]))+list(after.get('collections',[]))
    present=[KEY in row for row in rows]
    if any(present):
        if not all(present) or _view(before)!=_view(after):
            raise ValueError('Full native semantic collection/UV/paint/selection/proxy values changed')
    elif before!=after:
        raise ValueError('Full native collection changed without a complete semantic proof')
    return True

def proof_view(audit):
    """Stable full-value evidence; old native output remains byte-order strict."""
    present=[KEY in row for row in audit.get('collections',[])]
    if any(present):
        if not all(present):raise ValueError('Mixed native semantic proof availability')
        return _view(audit)
    return deepcopy(audit)

def publication_view(row,baseline,added_names=()):
    """Compare binding geometry while retaining existing artist config policy.

    Native physics configs author the fixed Property facade, its dirty bit and
    the collider reference. Explicit new simulation float maps may be added.
    Every other managed attribute remains covered by its full native hash.
    This function does not evaluate, rewrite or bake any authoring value.
    """
    if KEY not in row and KEY not in baseline:return deepcopy(row)
    if KEY not in row or KEY not in baseline:
        raise ValueError('Mixed native semantic proof availability in publication')
    current,previous=semantic(row),semantic(baseline)
    changed=current['groups']!=previous['groups']
    if changed==(current['blake3']==previous['blake3']):
        raise ValueError('Native semantic root hash contradicts its attribute inventory')
    result=proof_view({'collections':[row]})['collections'][0]
    old_attrs={(g['name'],a['name']) for g in previous['groups'] for a in g['attributes']}
    float_fields=('type_id','type_name','dependency','persistent','codec')
    float_schemas={tuple(a[key] for key in float_fields) for g in previous['groups']
                   if g['name']=='SimVertices3D' for a in g['attributes'] if a['type_name']=='float'}
    property_attrs={'KeyName','LowValue','HighValue','StringValue','Flags'}
    groups=[]
    for group in current['groups']:
        name=group['name'];entry=deepcopy(group)
        if name=='Property':
            if {a['name'] for a in entry['attributes']}!=property_attrs:
                raise ValueError('Unknown native physics Property facade schema')
            entry['count']=0
            for attr in entry['attributes']:
                attr['count']=0;attr.pop('blake3')
        elif name=='PropertyGlobal':
            if entry['count']!=1 or {a['name'] for a in entry['attributes']}!={'Dirty'}:
                raise ValueError('Unknown native physics dirty-state facade schema')
            for attr in entry['attributes']:attr.pop('blake3')
        else:
            retained=[]
            for attr in entry['attributes']:
                key=(name,attr['name'])
                if name=='SimVertices3D' and attr['name'] in added_names and key not in old_attrs:
                    if (attr['type_name']!='float' or attr['dependency'] or not attr['persistent'] or
                            tuple(attr[key] for key in float_fields) not in float_schemas):
                        raise ValueError('Published generated map is not a persistent native scalar float')
                    continue
                if key==('Lods','PhysicsAssetSoftObjectPathName'):attr.pop('blake3')
                retained.append(attr)
            entry['attributes']=retained
        groups.append(entry)
    # The full root changes with an explicitly permitted config/map delta;
    # all unmasked attribute hashes and all inventory metadata still compare.
    result[KEY]={'algorithm':current['algorithm'],'groups':groups}
    return result

def probe_semantics(audit,mutable_maps):
    """Keep every other managed attribute fingerprint during deliberate probes."""
    rows=audit['collections']
    if not any(KEY in row for row in rows):return None
    allowed={('SimVertices3D',name) for name in mutable_maps}
    allowed|={('Selection','KinematicVertices3D'),('RenderVertices','RenderDeformerSkinningBlend')}
    result=[]
    for row in rows:
        proof=semantic(row)
        groups=[]
        for group in proof['groups']:
            attrs=[]
            for attr in group['attributes']:
                current=dict(attr)
                if (group['name'],attr['name']) in allowed:current.pop('blake3')
                attrs.append(current)
            groups.append({'name':group['name'],'count':group['count'],'attributes':attrs})
        result.append({'lod':row['lod'],'groups':groups})
    return result
