import os
import numpy as np
import json
import soundfile
from collections import defaultdict
from tqdm import tqdm
from g2p_en import G2p


'''
import argparse
parser = argparse.ArgumentParser()
args = parser.parse_args()
'''
def merge_dict(first_dict, second_dict):
    third_dict = {**first_dict, **second_dict}
    return third_dict

def get_stats(numeric_list, prefix=""):
    # number, mean, standard deviation (std), median, mean absolute deviation
    stats_np = np.array(numeric_list)
    number = len(stats_np) 
    
    if number == 0:
        summ = 0.
        mean = 0.
        std = 0.
        median = 0.
        mad = 0.
        maximum = 0.
        minimum = 0.
    else:
        summ = np.sum(stats_np)
        mean = np.mean(stats_np)
        std = np.std(stats_np)
        median = np.median(stats_np)
        mad = np.sum(np.absolute(stats_np - mean)) / number
        maximum = np.max(stats_np)
        minimum = np.min(stats_np)
    
    stats_dict = {  prefix + "number": number, 
                    prefix + "mean": mean, 
                    prefix + "std": std, 
                    prefix + "median": median, 
                    prefix + "mad": mad, 
                    prefix + "summ": summ,
                    prefix + "max": maximum,
                    prefix + "min": minimum
                 }
    return stats_dict
    
    
class SpeechModel(object):
    def __init__(self, recog_dict, gop_result_dir, gop_json_fn):
        # STT
        self.recog_dict = recog_dict
        self.gop_ctm_info = self.get_gop_ctm(gop_result_dir, gop_json_fn)
        # Fluency
        self.sil_seconds = 0.145
        self.long_sil_seconds = 0.495
        self.disflunecy_words = ["AH", "UM", "UH", "EM", "OH"]
        self.special_words = ["<UNK>"]
        self.g2p = G2p()
    
    # STT features
    def recog(self, uttid):
        text = self.recog_dict[uttid]
        return text
    
    def get_ctm(self, uttid):
        ctm_info = self.gop_ctm_info[uttid]
        return ctm_info
    
    def get_phone_ctm(self, ctm_info):
        # use g2p model
        phone_ctm_info = []
        phone_text = []
        
        for word, start_time, duration, conf in ctm_info:
            phones = self.g2p(word)
            duration /= len(phones)
            
            for phone in phones:
                phone_ctm_info.append([phone, start_time, duration, conf])
                start_time += duration
                phone_text.append(phone)
        
        phone_text = " ".join(phone_text)
        
        return phone_ctm_info, phone_text
    
    def get_gop_ctm(self, gop_result_dir, gop_json_fn):
        # confidence (GOP)
        with open(gop_json_fn, "r") as fn:
            gop_json = json.load(fn)
        
        gop_ctm_info = defaultdict(list)
        
        # word-level ctm
        with open(os.path.join(gop_result_dir, "word.ctm")) as wctm_fn:
            count = 0
            prev_uttid = None
            for line in wctm_fn.readlines():
                uttid, _, start_time, duration, word_id = line.split()
                if prev_uttid != uttid:
                    count = 0
                # NOTE: 
                word_gop_id, word_gop_info = gop_json[uttid]["GOP"][count]
                word_gop = word_gop_info[-1][-1]
                
                assert word_id == word_gop_id
                
                start_time = round(float(start_time), 4)
                duration = round(float(duration), 4)
                conf = round(float(word_gop) / 100, 4)
                
                if conf > 1.0:
                    conf = 1.0
                if conf < 0.0:
                    conf = 0.0
                
                gop_ctm_info[uttid].append([word_id, start_time, duration, conf])
                count += 1
                prev_uttid = uttid
        
        return gop_ctm_info
    
    # Fluency features
    def sil_feats(self, ctm_info, total_duration):
        # > 0.145
        sil_list = []
        # > 0.495
        long_sil_list = []
        
        response_duration = total_duration
        if len(ctm_info) > 0:
            # response time
            start_time = ctm_info[0][1]
            # start_time + duration
            end_time = ctm_info[-1][1] + ctm_info[-1][2]
            response_duration = end_time - start_time        

        # word-interval silence
        if len(ctm_info) > 2:
            word, start_time, duration, conf = ctm_info[0]
            prev_end_time = start_time + duration
            
            for word, start_time, duration, conf in ctm_info[1:]:
                interval_word_duration = start_time - prev_end_time
                
                if interval_word_duration > self.sil_seconds:
                    sil_list.append(interval_word_duration)
                
                if interval_word_duration > self.long_sil_seconds:
                    long_sil_list.append(interval_word_duration)
                
                prev_end_time = start_time + duration
        
        
        sil_stats = get_stats(sil_list, prefix="sil_")
        long_sil_stats = get_stats(long_sil_list, prefix="long_sil_")
        '''
        {sil, long_sil}_rate1: num_silences / response_duration
        {sil, long_sil}_rate2: num_silences / num_words
        '''
        num_sils = len(sil_list)
        num_long_sils = len(long_sil_list)
        num_words = len(ctm_info)
        
        sil_stats["sil_rate1"] = num_sils / response_duration
        
        if num_words > 0:
            sil_stats["sil_rate2"] = num_sils / num_words
        else:
            sil_stats["sil_rate2"] = 0
        
        long_sil_stats["long_sil_rate1"] = num_long_sils / response_duration 
        
        if num_words > 0:
            long_sil_stats["long_sil_rate2"] = num_long_sils / num_words
        else:
            long_sil_stats["long_sil_rate2"] = 0
        
        sil_dict = merge_dict(sil_stats, long_sil_stats)
        
        return sil_dict, response_duration
    
    def word_feats(self, ctm_info, total_duration):
        '''
        TODO:
        number of repeated words (short pauses)
        distinct word
        articulation rate
        '''
        word_count_dict = defaultdict(int)
        word_duration_list = []
        word_conf_list = []
        num_disfluecy = 0
        num_repeat = 0
        prev_words = []
        
        response_duration = total_duration
        if len(ctm_info) > 0:
            # response time
            start_time = ctm_info[0][1]
            # start_time + duration
            end_time = ctm_info[-1][1] + ctm_info[-1][2]
            response_duration = end_time - start_time        
        
        for word, start_time, duration, conf in ctm_info:
            word_duration_list.append(duration)
            word_conf_list.append(conf)
            word_count_dict[word] += 1
            
            if word in self.disflunecy_words:
                num_disfluecy += 1
            
            if word in prev_words:
                num_repeat += 1
            
            prev_words = [word]
            
        # strat_time and duration of last word
        # word in articlulation time
        word_count = sum(list(word_count_dict.values()))
        word_distinct = len(list(word_count_dict.keys()))
        word_freq = word_count / response_duration
        word_duration_stats = get_stats(word_duration_list, prefix = "word_duration_")
        word_conf_stats = get_stats(word_conf_list, prefix="word_conf_")
        
        word_basic_dict = { 
                            "word_count": word_count,
                            "word_distinct": word_distinct,
                            "word_freq": word_freq,
                            "word_num_disfluency": num_disfluecy,
                            "word_num_repeat": num_repeat
                          }
        
        word_stats_dict = merge_dict(word_duration_stats, word_conf_stats)
        word_dict = merge_dict(word_basic_dict, word_stats_dict)
        
        return word_dict, response_duration
     
    def phone_feats(self, ctm_info, total_duration):
        phone_count_dict = defaultdict(int)
        phone_duration_list = []
        phone_conf_list = []
         
        response_duration = total_duration
        if len(ctm_info) > 0:
            # response time
            start_time = ctm_info[0][1]
            # start_time + duration
            end_time = ctm_info[-1][1] + ctm_info[-1][2]
            response_duration = end_time - start_time        
        
        for phone, start_time, duration, conf in ctm_info:
            phone_duration_list.append(duration)
            phone_conf_list.append(conf)
            phone_count_dict[phone] += 1  
            
        # strat_time and duration of last phone
        # word in articlulation time
        phone_count = sum(list(phone_count_dict.values()))
        phone_freq = phone_count / response_duration
        phone_duration_stats = get_stats(phone_duration_list, prefix = "phone_duration_")
        phone_conf_stats = get_stats(phone_conf_list, prefix="phone_conf_")
        
        phone_basic_dict = { 
                            "phone_count": phone_count,
                            "phone_freq": phone_freq,
                           }
        
        phone_stats_dict = merge_dict(phone_duration_stats, phone_conf_stats)
        phone_dict = merge_dict(phone_basic_dict, phone_stats_dict)
        
        return phone_dict, response_duration
