import os
import hashlib
import math
import re
import time
import gradio as gr

from PIL import Image, ImageFilter, ImageOps
import numpy as np

import modules 
import modules.processing
import modules.images as images

from modules import scripts, script_callbacks, shared, sd_samplers, masking
from modules.processing import Processed, StableDiffusionProcessingTxt2Img,StableDiffusionProcessingImg2Img,create_infotext,apply_overlay

from nai_api_gen import nai_api
from nai_api_gen import nai_api_processing

from modules import scripts, script_callbacks, shared, extra_networks, ui

from modules.ui_components import ToolButton

import modules.images as images
from modules.processing import process_images,apply_overlay,Processed
from PIL import Image, ImageFilter, ImageOps
from modules import masking
import numpy as np

from nai_api_gen.nai_api import NAIGenParams 

PREFIX = 'NAI'
hashdic = {}    

def get_api_key():
    return shared.opts.data.get('nai_api_key', None)

class NAIGENScriptBase(scripts.Script):

    def __init__(self):    
        super().__init__()
        self.NAISCRIPTNAME = "NAI"    
        self.images = []
        self.texts = []
        self.sampler_name = None
        self.api_connected = False
        self.disabled=False
        self.failed=False
        self.failure=""
        self.running=True
        self.do_nai_post=False        
        self.in_post_process=False
        self.width = 0
        self.height = 0
        self.mask = None
        self.cfg = 0
        self.steps = 0
        self.strength = 0
        # Experimental
        self.use_batch_processing = False
        self.hashes = []
        self.query_batch_size = 1
        
    def title(self):
        return self.NAISCRIPTNAME

    def show(self, is_img2img):
        return False
        
    def before_process(self, p,*args, **kwargs):
        nai_api_processing.patch_pi()        
        
    def postprocess(self, p,*args, **kwargs):
        nai_api_processing.unpatch_pi()
 
    def ui(self, is_img2img):        
        inpaint_label = "NAI Inpainting"
        inpaint_default = "Infill (No Denoise Strength)"
        inpaint_choices= [inpaint_default,"Img2Img (Use Denoise Strength)" ]
        with gr.Accordion(label=self.NAISCRIPTNAME, open=False):
            with gr.Row(variant="compact"):
                enable = gr.Checkbox(value=False, label="Enable")
                hr = gr.HTML()
                refresh = ToolButton(ui.refresh_symbol)
                refresh.click(fn= lambda: self.connect_api(), inputs=[], outputs=[enable,hr])
            with gr.Row(variant="compact", visible = is_img2img):
                modes = ["One Pass: NAI Img2Img/Inpaint", "Two Pass: NAI Txt2Img > Local Img2Img (Ignore Source Image)","Two Pass: NAI Img2Img/Inpaint > Local Img2Img"]
                do_local_img2img = gr.Dropdown(value=modes[0],choices= modes,type="index", label="Mode")
                if is_img2img:
                    inpaint_mode = gr.Dropdown(value=inpaint_default, label=inpaint_label , choices=inpaint_choices, type="index")
            with gr.Row(variant="compact"):
                model = gr.Dropdown(label= "Model",value=nai_api.NAIv3,choices=nai_api.nai_models,type="value",show_label=False)
                sampler = gr.Dropdown(label="Sampler",value="Auto",choices=["Auto",*nai_api.NAI_SAMPLERS],type="value",show_label=False)
            with gr.Row(variant="compact"):
                dynamic_thresholding = gr.Checkbox(value=False, label='Decrisper (Dynamic Thresholding)',min_width=64)
                smea = gr.Radio(label="SMEA",value="Off",choices=["SMEA","DYN","Off"],type="value",show_label=False)            
            with gr.Row(variant="compact",visible = is_img2img):
                img_resize_mode = gr.Dropdown(label='Resize Mode',  choices=["Resize","Crop","Fill","None (NAI Resize)"], value = "Resize", type="index")
                extra_noise=gr.Slider(minimum=0.0, maximum=1.0 ,step=0.01, label='Noise', value=0.0)
                add_original_image = gr.Checkbox(value=True, label='Inpaint: Overlay Image')            
            with gr.Accordion(label="Advanced", open=False):
                with gr.Row(variant="compact"):
                    cfg_rescale=gr.Slider(minimum=0.0, maximum=1.0, step=0.02, label='CFG Rescale', value=0.0)
                    uncond_scale=gr.Slider(minimum=0.0, maximum=1.5, step=0.05, label='Uncond Scale', value=1.0)
                    noise_schedule = gr.Dropdown(label="Schedule",value="recommended",choices=["recommended","exponential","polyexponential","karras","native"],type="value")
                    if not is_img2img:
                        inpaint_mode = gr.Dropdown(value=inpaint_default, label=inpaint_label , choices=inpaint_choices, type="index")

            with gr.Accordion(label='Local Second Pass Overrides: Ignored if 0', open=False , visible = is_img2img):                    
                with gr.Row(variant="compact"):
                    nai_resolution_scale=gr.Slider(minimum=0.0, maximum=4.0, step=0.05, label='Scale', value=1.0)
                    nai_cfg=gr.Slider(minimum=0.0, maximum=30, step=0.05, label='CFG', value=0.0)
                    nai_steps=gr.Slider(minimum=0, maximum=150, step=1, label='Steps', value=0)
                    nai_denoise_strength=gr.Slider(minimum=0.0, maximum=1.0,step=0.01, label='Denoise strength', value=0.0)
                    keep_mask_for_local = gr.Checkbox(value=False, label="Keep inpaint mask for both passes")
            with gr.Accordion(label="Options", open=False):
                with gr.Row(variant="compact"):
                    qualityToggle = gr.Radio(value="Off", label="Quality Preset",choices=["Off","On"],type="index") 
                    ucPreset = gr.Radio(label="Negative Preset",value="None",choices=["Heavy","Light","None"],type="index")           
                    convert_prompts = gr.Dropdown(label="Convert Prompts for NAI ",value="Auto",choices=["Auto","Never","Always"])
                    cost_limiter = gr.Checkbox(value=True, label="Force Opus Free Gen Size/Step Limit")
                    nai_post = gr.Checkbox(value=True, label="Use NAI for Inpainting with ADetailer")
        def on_enable(e,h):
            if e and not self.api_connected: return self.connect_api()
            return e,h
            
        if not self.skip_checks():
            enable.change(fn=on_enable, inputs=[enable,hr], outputs=[enable,hr])

        self.infotext_fields = [
            (enable, f'{PREFIX} enable I'),
            (sampler, f'{PREFIX} sampler'),
            (noise_schedule, f'{PREFIX} noise_schedule'),
            (dynamic_thresholding, f'{PREFIX} dynamic_thresholding'),
            (model, f'{PREFIX} '+ 'model'),
            (smea, f'{PREFIX} '+ 'smea'),
            (uncond_scale, f'{PREFIX} '+ 'uncond_scale'),
            (cfg_rescale, f'{PREFIX} '+ 'cfg_rescale'),
            
            (keep_mask_for_local, f'{PREFIX} '+ 'keep_mask_for_local'),
            (nai_denoise_strength, f'{PREFIX} '+ 'nai_denoise_strength'),
            (nai_steps, f'{PREFIX} '+ 'nai_steps'),
            (nai_cfg, f'{PREFIX} '+ 'nai_cfg'),
            (add_original_image, f'{PREFIX} '+ 'add_original_image'),
            (extra_noise, f'{PREFIX} '+ 'extra_noise'),
        ]
        
        self.paste_field_names = []
        for _, field_name in self.infotext_fields:
            self.paste_field_names.append(field_name)
            
        return [enable,convert_prompts,cost_limiter,nai_post,model,sampler,noise_schedule,dynamic_thresholding,smea,cfg_rescale,uncond_scale,qualityToggle,ucPreset,do_local_img2img,extra_noise,add_original_image,inpaint_mode,nai_resolution_scale,nai_cfg,nai_steps,nai_denoise_strength,img_resize_mode,keep_mask_for_local]

    def skip_checks(self):
        return shared.opts.data.get('nai_api_skip_checks', False)
                
    def get_api_key(self):
        return shared.opts.data.get('nai_api_key', None)
    
    def connect_api(self):
        s,m = self.subscription_status_message()
        self.api_connected=s
        return s, f'{m}'

    def check_api_key(self,skip_sub=False):    
        key = self.get_api_key()
        if key is None or len(key) < 6: return False
        if skip_sub or self.skip_checks(): return True
        status, opus, points, max = nai_api.subscription_status(key)
        return opus or points > 1   

    def subscription_status_message(self):
        key = self.get_api_key()
        status, opus, points, max = nai_api.subscription_status(key)
        #print(f'{status} {opus} {points} {max}')
        if status == -1: return False,"[API ERROR] Missing API Key, enter in options menu"
        elif status == 401: return False,"Invalid API Key"
        elif status != 200: return False,f"[API ERROR] Error Code: {status}"
        elif not opus and points <=1:
            return True, f'[API ERROR] Insufficient points! {points}'
        return True, f'[API OK] Anlas:{points} {"Opus" if opus else ""}'
    
    def setup_sampler_name(self,p, nai_sampler):
        if nai_sampler not in nai_api.NAI_SAMPLERS:
            nai_sampler = self.get_nai_sampler(p.sampler_name)
            p.sampler_name = sd_samplers.all_samplers_map.get(nai_sampler,None) or p.sampler_name
        self.sampler_name = nai_sampler
        
    
    def get_nai_sampler(self,sampler_name):
        sampler = sd_samplers.all_samplers_map.get(sampler_name)
        if sampler.name in ["DDIM","PLMS","UniPC"]: return "ddim_v3"
        for n in nai_api.NAI_SAMPLERS:
            if n in sampler.aliases:
                return n
        return shared.opts.data.get('NAI_gen_default_sampler', 'k_euler')
        
    def initialize(self):
        self.failed= False
        self.failure =""
        self.disabled= False
        self.images=[]
        self.hashes=[]
        self.texts=[]
        self.mask = None
        self.init_masked = None
        self.crop = None
        self.init_images = None
        
    def comment(self, p , c):
        print (c)
        if p is None or not hasattr(p, "comment"):return        
        p.comment(c)    
        
    def fail(self, p, c):
        self.comment(p,c)
        self.failed=True
        self.failure = c
        self.disabled=True
    
    def limit_costs(self, p, nai_batch = False):
        MAXSIZE = 1048576
        if p.width * p.height > MAXSIZE:
            scale = p.width/p.height
            p.height= int(math.sqrt(MAXSIZE/scale))
            p.width= int(p.height * scale)
                
            p.width = int(p.width/64)*64
            p.height = int(p.height/64)*64
        
            self.comment(p,f"Cost Limiter: Reduce dimensions to {p.width} x {p.height}")
            
        if nai_batch and p.batch_size > 1:
            p.n_iter *= p.batch_size
            p.batch_size = 1
            self.comment(p,f" Cost Limiter: Disable Batching")
        if p.steps >28: 
            p.steps = 28
            self.comment(p,f"Cost Limiter: Reduce steps to {p.steps}")
    
    def adjust_resolution(self, p):
        #if p.width % 64 == 0 and p.height % 64 == 0 or p.width *p.height > 1792*1728: return
        width = p.width
        height = p.height        
        
        width = int(p.width/64)*64
        height = int(p.height/64)*64
        
        MAXSIZE = 1792*1728
        
        if width *height > MAXSIZE:            
            scale = width/height
            width= int(math.sqrt(MAXSIZE/scale))
            height= int(width * scale)
            width = int(p.width/64)*64
            height = int(p.height/64)*64
        
        if width == p.width and height == p.height: return
        
        self.comment(p,f'Adjusted resolution from {p.width} x {p.height} to {width} x {height}- NAI dimensions must be multiples of 64 and <= 1792x1728')
        
        p.width = width
        p.height = height
        
    def nai_configuration(self,p,enable,convert_prompts,cost_limiter,nai_post,model,sampler,noise_schedule,dynamic_thresholding,smea,cfg_rescale,uncond_scale,qualityToggle,ucPreset,do_local_img2img,extra_noise,add_original_image,inpaint_mode,nai_resolution_scale,nai_cfg,nai_steps,nai_denoise_strength,img_resize_mode,keep_mask_for_local,**kwargs):        
        if not enable: self.disabled=True
        if self.disabled: return 
        
        if not self.check_api_key():
            self.fail(p,"Invalid NAI Key")
            return
            
        self.do_nai_post=nai_post            
        self.setup_sampler_name(p, sampler)        
        if cost_limiter: self.limit_costs(p)
        self.adjust_resolution(p)

        self.isimg2img =  getattr(p, "init_images",None) is not None
        if not self.isimg2img: do_local_img2img = 0
        self.do_local_img2img=do_local_img2img

        self.width = p.width
        self.height= p.height
        self.cfg = p.cfg_scale
        self.steps = p.steps
        self.strength = getattr(p,"denoising_strength",0)
        self.mask = getattr(p,"image_mask",None)
        
        if  do_local_img2img== 1 or do_local_img2img == 2:
            self.set_local(p,enable,convert_prompts,cost_limiter,nai_post,model,sampler,noise_schedule,dynamic_thresholding,smea,cfg_rescale,uncond_scale,qualityToggle,ucPreset,do_local_img2img,extra_noise,add_original_image,inpaint_mode,nai_resolution_scale,nai_cfg,nai_steps,nai_denoise_strength,img_resize_mode,keep_mask_for_local)
        elif not self.use_batch_processing:
            p.disable_extra_networks=True
            p.batch_size = p.n_iter * p.batch_size
            p.n_iter = 1

        p.nai_processed=None         
        
    def restore_local(self,p):
        p.width = self.width
        p.height= self.height
        p.cfg_scale = self.cfg
        p.steps = self.steps
        p.image_mask = self.mask
        p.denoising_strength = self.strength
        
    def set_local(self,p,enable,convert_prompts,cost_limiter,nai_post,model,sampler,noise_schedule,dynamic_thresholding,smea,cfg_rescale,uncond_scale,qualityToggle,ucPreset,do_local_img2img,extra_noise,add_original_image,inpaint_mode,nai_resolution_scale,nai_cfg,nai_steps,nai_denoise_strength,img_resize_mode,keep_mask_for_local):    
        if nai_resolution_scale> 0:
            p.width = int(p.width * nai_resolution_scale)
        p.height = int(p.height * nai_resolution_scale)
        if nai_cfg > 0: p.cfg_scale = nai_cfg
        if nai_steps > 0: p.steps = nai_steps
        if nai_denoise_strength > 0 and has_attr(p,"denoising_strength") : p.denoising_strength = nai_denoise_strength
        if not keep_mask_for_local and do_local_img2img == 2 and has_attr(p,"image_mask"): p.image_mask = None

    def nai_preprocess(self,p,enable,convert_prompts,cost_limiter,nai_post,model,sampler,noise_schedule,dynamic_thresholding,smea,cfg_rescale,uncond_scale,qualityToggle,ucPreset,do_local_img2img,extra_noise,add_original_image,inpaint_mode,nai_resolution_scale,nai_cfg,nai_steps,nai_denoise_strength,img_resize_mode,keep_mask_for_local,**kwargs):
        if not enable: self.disabled=True
        if self.disabled: return 
        isimg2img=self.isimg2img
        do_local_img2img=self.do_local_img2img        
        if isimg2img and do_local_img2img == 0 and p.denoising_strength == 0:
            for i in range(len(p.init_images)):
                self.images.append(p.init_images[i])
                self.texts.append(self.infotext(p,i))
            p.nai_processed = Processed(p, self.images, p.seed, self.texts[0], subseed=p.all_subseeds[0], infotexts = self.texts) 
            return
        
        mask = None if not isimg2img else (p.image_mask or self.mask)  
        init_masked = None
        
        crop = None
        init_images=[]   
        if isimg2img:            
            for i in range(len(p.init_images)):
                image = images.flatten(p.init_images[i], shared.opts.img2img_background_color)
                if not p.inpaint_full_res:
                    image = images.resize_image(p.resize_mode if p.resize_mode < 3 else 0, image, p.width, p.height)
                init_images.append(image)
                
            if mask is not None: 
                mask = mask.convert('L')
                print(mask.width, mask.height)
                if p.inpainting_mask_invert: mask = ImageOps.invert(mask)
                if p.mask_blur > 0: mask = mask.filter(ImageFilter.GaussianBlur(p.mask_blur))
                if p.inpaint_full_res:
                    overlay_mask = mask
                    crop = masking.expand_crop_region(masking.get_crop_region(np.array(mask), p.inpaint_full_res_padding), p.width, p.height, mask.width, mask.height)
                    mask = images.resize_image(2, mask.crop(crop), p.width, p.height)
                else:
                    mask = images.resize_image(p.resize_mode if p.resize_mode < 3 else 0, mask, p.width, p.height)
                    overlay_mask = Image.fromarray(np.clip((np.array(mask).astype(np.float32)) * 2, 0, 255).astype(np.uint8))
                mask = mask.convert('L')
                
                init_masked=[]
                for i in range(len(p.init_images)):
                    image = p.init_images[i]
                    image_masked = Image.new('RGBa', (image.width, image.height))
                    print(image.width, image.height, p.width, p.height, mask.width, mask.height, overlay_mask.width, overlay_mask.height)
                    image_masked.paste(image.convert("RGBA").convert("RGBa"), mask=ImageOps.invert(overlay_mask.convert('L')))
                    init_masked.append(image_masked.convert('RGBA'))
                    
                self.mask = mask
                self.init_masked = init_masked
                self.crop = crop
            self.init_images = init_images
        else: self.init_images = None
               
    
    def nai_image_processsing(self,p, *args, **kwargs):
        self.nai_preprocess(p, *args, **kwargs)
        if not self.use_batch_processing: self.nai_generate_images(p, *args, **kwargs)
            
    def convert_to_nai(self, prompt, neg,convert_prompts="Always"):
        if convert_prompts != "Never":
            if convert_prompts == "Always" or nai_api.prompt_has_weight(prompt): prompt = nai_api.prompt_to_nai(prompt)
            if convert_prompts == "Always" or nai_api.prompt_has_weight(prompt): neg = nai_api.prompt_to_nai(neg)
            prompt=prompt.replace('\\(','(').replace('\\)',')')
            neg=neg.replace('\\(','(').replace('\\)',')')
        return prompt, neg
        
    def infotext(self,p,i):
        iteration = int(i / (p.n_iter*p.batch_size))
        batch = i % p.batch_size            
        return create_infotext(p, p.all_prompts, p.all_seeds, p.all_subseeds, None, iteration, batch)


    def nai_generate_images(self,p,enable,convert_prompts,cost_limiter,nai_post,model,sampler,noise_schedule,dynamic_thresholding,smea,cfg_rescale,uncond_scale,qualityToggle,ucPreset,do_local_img2img,extra_noise,add_original_image,inpaint_mode,nai_resolution_scale,nai_cfg,nai_steps,nai_denoise_strength,img_resize_mode,keep_mask_for_local,**kwargs):
        if self.disabled or p.nai_processed is not None: return 
        
        isimg2img=self.isimg2img
        do_local_img2img=self.do_local_img2img
        
        model = getattr(p,f'{PREFIX}_'+ 'model',model)
        smea = getattr(p,f'{PREFIX}_'+ 'smea',smea)
        sampler = getattr(p,f'{PREFIX}_'+ 'sampler',None) or self.sampler_name        
        noise_schedule = getattr(p,f'{PREFIX}_'+ 'noise_schedule',noise_schedule)
        dynamic_thresholding = getattr(p,f'{PREFIX}_'+ 'dynamic_thresholding',dynamic_thresholding)
        uncond_scale = getattr(p,f'{PREFIX}_'+ 'uncond_scale',uncond_scale)
        cfg_rescale = getattr(p,f'{PREFIX}_'+ 'cfg_rescale',cfg_rescale)        
        extra_noise = getattr(p,f'{PREFIX}_'+ 'extra_noise',extra_noise)        
        add_original_image = getattr(p,f'{PREFIX}_'+ 'add_original_image',add_original_image)        
        
        p.extra_generation_params[f'{PREFIX} enable I'] = True
        if sampler.lower() != "auto": p.extra_generation_params[f'{PREFIX} sampler'] = sampler
        p.extra_generation_params[f'{PREFIX} noise_schedule'] = noise_schedule
        p.extra_generation_params[f'{PREFIX} dynamic_thresholding'] = dynamic_thresholding
        p.extra_generation_params[f'{PREFIX} '+ 'smea'] = smea
        p.extra_generation_params[f'{PREFIX} '+ 'uncond_scale'] = uncond_scale
        p.extra_generation_params[f'{PREFIX} '+ 'cfg_rescale'] = cfg_rescale
        p.extra_generation_params[f'{PREFIX} '+ 'keep_mask_for_local'] = keep_mask_for_local
        
        if do_local_img2img != 0:        
            if nai_denoise_strength!= 0: p.extra_generation_params[f'{PREFIX} '+ 'nai_denoise_strength'] = nai_denoise_strength
            if nai_steps!= 0: p.extra_generation_params[f'{PREFIX} '+ 'nai_steps'] = nai_steps
            if nai_cfg!= 0: p.extra_generation_params[f'{PREFIX} '+ 'nai_cfg'] = nai_cfg
            if nai_resolution_scale!= 1: p.extra_generation_params[f'{PREFIX} '+ 'nai_resolution_scale'] = nai_resolution_scale
            
        if isimg2img:
            p.extra_generation_params[f'{PREFIX} '+ 'add_original_image'] = add_original_image
            p.extra_generation_params[f'{PREFIX} '+ 'extra_noise'] = extra_noise
            p.extra_generation_params[f'{PREFIX} '+ 'img_resize_mode'] = img_resize_mode        
            
        extra_noise = max(getattr(p,"extra_noise",0) , extra_noise)

        def getparams(i):
            seed =int(p.all_seeds[i])
            
            image= None if ( not isimg2img or do_local_img2img == 1 or self.init_images is None or len(self.init_images) == 0) else  self.init_images[len(self.init_images) % min(p.batch_size, len (self.init_images))]
            
            if self.crop is not None:
                image = image.crop(self.crop)
                image = images.resize_image(2, image, p.width, p.height)
            elif image is not None and img_resize_mode < 3:
                image = images.resize_image(img_resize_mode, image, p.width, p.height)
            
            prompt,neg = self.convert_to_nai(p.all_prompts[i],  p.all_negative_prompts[i], convert_prompts)
            
            return NAIGenParams(prompt, neg, seed=seed , width=p.width, height=p.height, scale=p.cfg_scale, sampler = self.sampler_name, steps=p.steps, noise_schedule=noise_schedule,sm=smea.lower()=="smea", sm_dyn="dyn" in smea.lower(), cfg_rescale=cfg_rescale,uncond_scale=uncond_scale ,dynamic_thresholding=dynamic_thresholding,model=model,qualityToggle = qualityToggle == 1, ucPreset = ucPreset , noise = extra_noise, image = image, strength= p.denoising_strength,overlay=add_original_image, mask = self.mask if inpaint_mode!=1 else None)
        
        self.get_batch_images(p, getparams, save_images = isimg2img and getattr(p,"inpaint_full_res") and shared.opts.data.get('nai_api_save_fragments', False), save_suffix ="-nai-init-image" if do_local_img2img > 0 else "" ,dohash = False, query_batch_size=self.query_batch_size)
        
        if not self.use_batch_processing:
            if do_local_img2img == 0:
               p.nai_processed = Processed(p, self.images, p.seed, self.texts[0], subseed=p.all_subseeds[0], infotexts = self.texts) 
            else:
                self.all_seeds = p.all_seeds.copy()
                self.all_subseeds = p.all_subseeds.copy()
                self.all_prompts = p.all_prompts.copy()
                self.all_negative_prompts = p.all_negative_prompts.copy()
                
                self.set_local(p,enable,convert_prompts,cost_limiter,nai_post,model,sampler,noise_schedule,dynamic_thresholding,smea,cfg_rescale,uncond_scale,qualityToggle,ucPreset,do_local_img2img,extra_noise,add_original_image,inpaint_mode,nai_resolution_scale,nai_cfg,nai_steps,nai_denoise_strength,img_resize_mode,keep_mask_for_local)
                    
                p.init_images = self.images
                self.include_nai_init_images_in_results=True            
            
    
    def get_batch_images(self, p, getparams, save_images = False , save_suffix = "", dohash = False, query_batch_size = 1, is_post = False):          
        key = get_api_key()
               
        cur_iter = p.iteration
        iter_count = p.n_iter
        batch_size = p.batch_size
        if not self.use_batch_processing:
            if self.do_local_img2img > 0:
                iter_count = 1
            else:
                cur_iter = cur_iter * batch_size
                batch_size = batch_size*iter_count
                iter_count = 1
        
        while len(self.images) < cur_iter*batch_size + batch_size:
            results=[]
            resultsidx=[]
            for i in range( len(self.images) , min(len(self.images) + query_batch_size,  iter_count*batch_size) ):        
                parameters = getparams(i)
                if dohash and len(parameters) < 10000 and parameters in hashdic:
                    hash = hashdic[parameters]
                    imgp = os.path.join(shared.opts.outdir_init_images, f"{hash}.png")
                    if os.path.exists(imgp):
                        print("Loading Previously Generated Image")
                        self.images.append(Image.open(imgp))
                        self.hashes.append(None)
                        self.texts.append(self.infotext(p,i))
                else:
                    self.images.append(None)
                    self.hashes.append(None)
                    self.texts.append("")
                    strip = re.sub("\"image\":\".*?\"","\"image\":\"\"" ,re.sub("\"mask\":\".*?\"","\"mask\":\"\"" ,parameters))
                    print(f'{strip}')                      
                    
                    results.append(nai_api.POST(key, parameters, g = query_batch_size > 1))
                    resultsidx.append(i)               
                
                    
            if query_batch_size > 1: 
                import grequests    
                results = grequests.map(results)
            

            for ri in range(len(results)):
                result = results[ri]
                i = resultsidx[ri]
                image,code =  nai_api.LOAD(result, parameters)
                #TODO: Handle time out errors
                if image is None: 
                    self.texts[i] = code
                    continue
                if dohash:
                    hash = hashlib.md5(image.tobytes()).hexdigest()
                    self.hashes[i] = hash
                    if not getattr(p, "use_txt_init_img",False): p.extra_generation_params["txt_init_img_hash"] = hash
                    if len(parameters) < 10000: hashdic[parameters] = hash
                    if not os.path.exists(os.path.join(shared.opts.outdir_init_images, f"{hash}.png")):
                        images.save_image(image, path=shared.opts.outdir_init_images, basename=None, extension='png', forced_filename=hash, save_to_dirs=False)
                self.images[i] = image
                self.texts[i] = self.infotext(p,i)
                    
                if save_images:
                    images.save_image(image, p.outpath_samples, "", p.all_seeds[i], p.all_prompts[i], shared.opts.samples_format, info=self.texts[i], suffix=save_suffix)
                
        for i in range(cur_iter*batch_size, cur_iter*batch_size+batch_size):
            if self.images[i] is None:
                if iter_count*batch_size == 1:
                    self.fail(p,f'Failed to retrieve image - Error Code: {self.texts[i]}')
                else: self.comment(p,f'Failed to retrieve image {i} - Error Code: {self.texts[i]}')
                print("Image Failed to Load, Giving Up")
                if dohash and batch_size * iter_count == 1:  p.enable_hr = False
                self.images[i] = Image.new("RGBA",(p.width, p.height), color = "black")
            else:
                if i == 0 and save_images and not is_post:
                    import modules.paths as paths
                    with open(os.path.join(paths.data_path, "params.txt"), "w", encoding="utf8") as file:
                        file.write(Processed(p, []).infotext(p, 0))
                if self.crop is not None:
                    crop = self.crop
                    fragments = self.images.copy() if shared.opts.data.get('nai_api_all_images', False) else None
                    for i in range(len(self.images)):
                        image = apply_overlay(self.images[i],  (self.crop[0], self.crop[1], self.crop[2]-self.crop[0], self.crop[3]-self.crop[1]), 0, self.init_masked)
                        self.images[i] = image
                    if fragments is not None:
                        self.images+=fragments
                        self.texts*=2
                elif self.mask is not None:
                    image = apply_overlay(self.images[i], None, 0, self.init_masked)
