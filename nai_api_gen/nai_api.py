from modules import scripts, script_callbacks, shared, processing, extra_networks
import os
from io import BytesIO
from zipfile import ZipFile
from PIL import Image 

import hashlib
import math
import re
import base64
import time

NAIv1 = "nai-diffusion"
NAIv1c = "safe-diffusion"
NAIv1f = "nai-diffusion-furry"
NAIv2 = "nai-diffusion-2"
NAIv3 = "nai-diffusion-3"

nai_models = [NAIv3,NAIv2,NAIv1,NAIv1c,NAIv1f]

NAI_SAMPLERS = ["k_euler","k_euler_ancestral","k_dpmpp_2s_ancestral","k_dpmpp_2m","ddim_v3","k_dpmpp_sde"]
noise_schedules = ["exponential","polyexponential","karras","native"]

noise_schedule_selections = ["recommended","exponential","polyexponential","karras","native"]

def get_headers(key):
    return {
        'accept': "*/*",
        'accept-language': "en-US,en;q=0.9,ja;q=0.8",   
        'Authorization':"Bearer "+ key,     
        'content-type': "application/json",
        'sec-ch-ua': "\"Google Chrome\";v=\"119\", \"Chromium\";v=\"119\", \"Not?A_Brand\";v=\"24\"",
        'sec-ch-ua-mobile': "?0",
        'sec-ch-ua-platform': "\"Windows\"",
        'sec-fetch-dest': "empty",
        'sec-fetch-mode': "cors",
        'sec-fetch-site': "same-site"        
    }
def POST(key,parameters, g =False):          
    headers = get_headers(key)
    parameters = parameters.encode()
    if g: 
        import grequests
        import requests
        return grequests.post('https://api.novelai.net/ai/generate-image',headers=headers, data=parameters)
    import requests
    return requests.post('https://api.novelai.net/ai/generate-image',headers=headers, data=parameters)

def LOAD(response,parameters):
    if response.status_code == 200:
        with ZipFile(BytesIO(response.content)) as zip_file:
            file_list = zip_file.namelist()
            image_file_name = file_list[0]
            image_data = zip_file.read(image_file_name)
            image = Image.open(BytesIO(image_data))
            return image, 200
    else:
        if response.status_code==400:
            print(f'400 Invalid Response, check prompt for invalid characters.')
        if response.status_code==500:
            print(f'400 Invalid Response, check prompt for invalid characters.')
        else: print(f"Failure: {response.status_code}")
        return None, response.status_code


def tryfloat(value, default = None):
    try:
        value = value.strip()
        return float(value)
    except Exception as e:
        #print(f"Invalid Float: {value}")
        return default
        
def prompt_has_weight(p):
    uo = '('
    ux = ')'
    e=':'
    eidx = None
    inparen=0
    for i in range(len(p)):
        c = p[i]
        if c in [uo]:
            if i>0 and p[i]=='\\': continue
            inparen+=1
        elif c == e:
            eidx = i
        elif c == ux and inparen>0:
            inparen-=1
            if eidx is None: continue
            weight = tryfloat(p[eidx+1:i],None)
            if weight is not None:return True
    return False
    
def prompt_is_nai(p):
    return "{" in p
    
def subscription_status(key):
    if not key:
        return -1,False,0,0        
    import requests    
    response = requests.get('https://api.novelai.net/user/subscription',headers=get_headers(key))
    try:
        if response.status_code==200:
            content = response.json()
            def max_unlimited():
                max = 0
                for l in content['perks']['unlimitedImageGenerationLimits']:
                    if l['maxPrompts'] > 0 and l['resolution'] >= max: 
                        max = l['resolution']
                return max
            max = max_unlimited()
            active = content['active']
            
            unlimited = active and max >= 1048576
            
            points = content['trainingStepsLeft']['fixedTrainingStepsLeft']+content['trainingStepsLeft']['purchasedTrainingSteps']
            
            return response.status_code, unlimited, points, max
        elif response.status_code==401: 
            print ("Invalid Key")
        else:
            print (response.status_code)
    except requests.exceptions.JSONDecodeError:
        pass
    return response.status_code,False,0,0
    
def prompt_to_nai(p, parenthesis_only = False):
    chunks = []
    out = ""
    states = []
    start = 0
    state = None
    do = '['
    dx = ']'
    uo = '('
    ux = ')'
    e=':'
    
    
    def addtext(i, end,prefix = "", suffix = ""):
        nonlocal out
        nonlocal state
        nonlocal start
        if state is None:
            s = p[start:i]
        elif state[3] is not None:
            s = state[3] + p[state[0]:i]
        else: s = p[state[0]:i]
        
        s = f'{prefix}{s}{suffix}'
        #print (s)
        if len(states)>0:
            next = states.pop()
            next[3] += s
            next[0] = end
            state = next
        else:
            state = None        
            out+=s
            
        start = end
        
    def adjustments(v):
        if v==1: return 1
        if v<=0: return 25
        if v < 1: v = 1/v
        m = 1
        for i in range(0,25):
            dif = v - m
            m*=1.05
            if v < m and dif <= m - v: return i
        return 25
    
    idx = 0
    while idx < len(p) or state is not None:
        if idx < len(p): 
            i = idx
            c = p[i]
        else: 
            c = ux if state[1] == uo else dx
            i = len(p)
        idx+=1        
        if c not in [do,uo,dx,ux,e] or i>0 and p[i-1] == '\\': continue    
        if c in [do,uo]:
            if parenthesis_only and c == do: continue
            if state is None: addtext(i,i+1)
            else:     
                state[3] += p[state[0]:i] 
                state[0] = i+1
                states.append(state)
            state = [i+1,c,None,""]                
        elif state is None: continue        
        elif c == e: state[2] = i
        elif c == dx and not parenthesis_only: addtext(i,i+1,'[[',']]' )
        elif c == ux:
            if state[2] is not None:
                numstart = state[2]+1
                numend = i
                weight = tryfloat(p[state[2]+1:i],None)
                if weight is not None:
                    adj = adjustments(weight)
                    if abs(weight - 1) < 0.025: addtext(state[2],i+1)
                    elif weight < 1: addtext(state[2],i+1,'['*adj ,']'*adj )
                    else: addtext(state[2],i+1,'{'*adj,'}'*adj )
                    continue
            if parenthesis_only: addtext(i,i+1,'(')
            else: addtext(i,i+1,'{{','}}')
    if start<len(p): addtext(len(p),len(p))
    if not parenthesis_only: out = out.replace("\\(","(").replace("\\)",")")
    return out
    
    
def NAIGenParams(prompt, neg, seed, width, height, scale, sampler, steps, noise_schedule, dynamic_thresholding= False, sm= False, sm_dyn= False, cfg_rescale=0,uncond_scale =1,model =NAIv3 ,image = None, noise=None, strength=None ,extra_noise_seed=None, mask = None,qualityToggle=False,ucPreset = 2,overlay = False):
    def clean(p):
        if type(p) != str: p=f'{p}'
        #TODO: Look for a better way to do this        
        p=re.sub("(?<=[^\\\\])\"","\\\"" ,p)
        p=re.sub("\r?\n"," " ,p)
       ## p=re.sub("\s"," " ,p)
        return p
    prompt=clean(prompt)
    neg=clean(neg)
    
    if type(uncond_scale) != float and type (uncond_scale) != int: uncond_scale = 1.0
    if type(cfg_rescale) != float and type (cfg_rescale) != int: cfg_rescale = 0.0
    
    if prompt == "": prompt = " "    
    if model not in nai_models: model = NAIv3
    
    if "ddim" in sampler.lower() or model != NAIv3: 
        noise_schedule=""
    else:
        if noise_schedule.lower() not in ["exponential","polyexponential","karras","native"]: 
            if sampler != "k_dpmpp_2m": noise_schedule = "native" 
            else:  noise_schedule = "exponential"
        if "_a" in sampler and noise_schedule == "karras": noise_schedule = "native"
        noise_schedule = f',"noise_schedule":"{noise_schedule}"'
    
    cfg_rescale = f',"cfg_rescale":{cfg_rescale}' if model == NAIv3 else ""
    uncond_scale = f',"uncond_scale":{uncond_scale}' if model == NAIv3 or model == NAIv2 else ""
        
    if qualityToggle:
        if model == NAIv3:
            #tags = 'best quality\s*,\s.*amazing\s.*quality\s.*,\s.*very aesthetic\s.*,\s.*absurdres'
            tags = 'best quality, amazing quality, very aesthetic, absurdres'
            if tags not in prompt: prompt = f'{prompt}, {tags}'
        elif model == NAIv2:
            tags = 'very aesthetic, best quality, absurdres'
            if not prompt.startswith(tags):
                prompt = f'{tags}, {prompt}'
        else:
            tags = 'masterpiece, best quality'
            if not prompt.startswith(tags):
                prompt = f'{tags}, {prompt}'    
    if ucPreset == 0:
        if model == NAIv3:
            tags = 'lowres, {bad}, error, fewer, extra, missing, worst quality, jpeg artifacts, bad quality, watermark, unfinished, displeasing, chromatic aberration, signature, extra digits, artistic error, username, scan, [abstract]'
        elif model == NAIv2:
            tags = 'lowres, bad, text, error, missing, extra, fewer, cropped, jpeg artifacts, worst quality, bad quality, watermark, displeasing, unfinished, chromatic aberration, scan, scan artifacts'
        else:
            tags = 'lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry'
        if tags not in neg: neg = f'{tags}, {neg}'
    
    if ucPreset == 1:
        if model == NAIv3 or model == NAIv2:
            tags = 'lowres, jpeg artifacts, worst quality, watermark, blurry, very displeasing'
        else:
            tags = 'lowres, text, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry'
        if tags not in neg: neg = f'{tags}, {neg}'
    
    if isinstance(image, Image.Image):            
        #image=image.convert(mode="RGBA")
        image_byte_array = BytesIO()
        image.save(image_byte_array, format='PNG')
        image = base64.b64encode(image_byte_array.getvalue()).decode("utf-8")
    
    if image is not None:    
        action = 'img2img'
        image = f',"image":"{image}"'
        strength = f',"strength":{strength or 0.5}'
        noise = f',"noise":{noise or 0}'
        extra_noise_seed = f',"extra_noise_seed":{extra_noise_seed or seed}'        
        if isinstance(mask, Image.Image):
            image_byte_array = BytesIO()
            mask.save(image_byte_array, format="PNG")
            mask = base64.b64encode(image_byte_array.getvalue()).decode("utf-8")
        if mask is not None:
            model += "-inpainting"
            mask = f',"mask":"{mask}"'
            
            action="infill"
            sm=False
            sm_dyn=False
            if "ddim" in sampler.lower():
                print("DDIM Not supported for Inpainting, switching to Euler")
                sampler = "k_euler"
        #else: overlay = False
    else:
        strength = ""
        noise=""
        image=""
        extra_noise_seed=""
        action = 'generate'
        
    dynamic_thresholding = "true" if dynamic_thresholding else "false"
    sm = "true" if sm else "false"
    sm_dyn = "true" if sm_dyn else "false"
    qualityToggle = "true" if qualityToggle else "false"
    
    return f'{{"input":"{prompt}","model":"{model}","action":"{action}","parameters":{{"width":{int(width)},"height":{int(height)},"scale":{scale},"sampler":"{sampler}","steps":{steps},"seed":{int(seed)},"n_samples":1{strength or ""}{noise or ""},"ucPreset":{ucPreset},"qualityToggle":"{qualityToggle}","sm":"{sm}","sm_dyn":"{sm_dyn}","dynamic_thresholding":"{dynamic_thresholding}","controlnet_strength":1,"legacy":"false","add_original_image":"{overlay}"{uncond_scale or ""}{cfg_rescale or ""}{noise_schedule or ""}{image or ""}{mask or ""}{extra_noise_seed or ""},"negative_prompt":"{neg}"}}}}'

def noise_schedule_selected(sampler,noise_schedule):
    noise_schedule=noise_schedule.lower()
    sampler=sampler.lower()
    
    if noise_schedule not in noise_schedule or sampler == "ddim": return False
    
    if sampler == "k_dpmpp_2m": return noise_schedule != "exponential"                 
    return noise_schedule != "native" 

def get_set_noise_schedule(sampler,noise_schedule):
    if sampler == "ddim": return ""
    if noise_schedule_selected(sampler, noise_schedule): return noise_schedule
    return noise_schedule_selections[0]

def convert(input):
    #Chat Shit, broken, works half the time.
    
    re_attention = re.compile(r'\{|\[|\}|\]|[^\{\}\[\]]+', re.MULTILINE | re.UNICODE)
    text = input.replace("(", r"\(").replace(")", r"\)").replace(r'\\{2,}(\(|\))', r"\\$1")

    res = []
    curly_brackets = []
    square_brackets = []

    curly_bracket_multiplier = 1.05
    square_bracket_multiplier = 1 / 1.05

    def multiply_range(start_position, multiplier):
        for pos in range(start_position, len(res)):
            res[pos][1] = round(res[pos][1] * multiplier * 10000) / 10000

    for match in re_attention.finditer(text):
        word = match.group(0)

        if word == "{":
            curly_brackets.append(len(res))
        elif word == "[":
            square_brackets.append(len(res))
        elif word == "}" and curly_brackets:
            multiply_range(curly_brackets.pop(), curly_bracket_multiplier)
        elif word == "]" and square_brackets:
            multiply_range(square_brackets.pop(), square_bracket_multiplier)
        else:
            res.append([word, 1.0])

    for pos in curly_brackets:
        multiply_range(pos, curly_bracket_multiplier)

    for pos in square_brackets:
        multiply_range(pos, square_bracket_multiplier)

    if not res:
        res = [["", 1.0]]

    i = 0
    while i + 1 < len(res):
        if res[i][1] == res[i + 1][1]:
            res[i][0] = res[i][0] + res[i + 1][0]
            res.pop(i + 1)
        else:
            i += 1

    result = ""
    for item in res:
        if item[1] == 1.0:
            result += item[0]
        else:
            result += f"({item[0]}:{item[1]})"
    
    return result
    
    
    