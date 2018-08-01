#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Jul 27 20:15:22 2018

@author: rem
"""

import random
import torch.utils.data

class Index(object):
  
  def __init__(self, initwords = [], unkindex = None):
    self.id2w = []
    self.w2id = {}
    self.frozen = False
    self.unkindex = unkindex
    if initwords is not None:
      for w in initwords:
        self.add(w)

  def add(self, word):
    if word in self.w2id:
      return self.w2id[word]
    if self.frozen:
      if not self.silentlyfrozen:
        raise ValueError('Index can not be altered anymore. It is already frozen.')
      else:
        return self.unkindex
    idx = len(self.id2w)
    self.w2id[word] = idx
    self.id2w.append(word)
    return idx
  
  def size(self):
    return len(self.w2id)
  
  def hasId(self, idx):
    return idx >= 0 and idx < len(self.id2w)
  
  def hasWord(self, word):
    return word in self.w2id

  def getWord(self, index):
    return self.id2w[index]
    
  def getId(self, word):
    try:
      return self.w2id[word]
    except KeyError:
      return self.unkindex
  
  def tofile(self, fname):
    with open(fname, 'w') as f:
      lines = map(lambda w: w + '\n', self.id2w)
      f.writelines(lines)
      
  def freeze(self, silent = False):
    self.frozen = True
    self.silentlyfrozen = silent
    return self
  
  def vocabulary(self):
    return self.id2w
  
  def __contains__(self, key):
    if isinstance(key, str):
      return self.hasWord(key)
    return self.hasId(key)

  def __getitem__(self, key):
    # return the id if we get a word
    if isinstance(key, str):
      return self.getId(key)
    # return the word if we get an id, lets assume that 'key' is some kind of number, i.e. int, long, ...
    if not hasattr(key, '__iter__'):
      return self.getWord(key)
    # otherwise recursively apply this method for every key in an iterable
    return map(lambda k: self[k], key)
  
  def __iter__(self):
    return self.id2w.__iter__()

  def __len__(self):
    return self.size()
  
  @staticmethod
  def fromfile(fname):
    index = Index()
    with open(fname, 'r', encoding='utf8') as f:
      for i, line in enumerate(f):
        w = line.rstrip()
        index.id2w.append(w)
        index.w2id[w] = i
    return index

class RandomBatchSampler(torch.utils.data.sampler.BatchSampler):
  
  def __init__(self, *args, **kwargs):
    super(RandomBatchSampler, self).__init__(*args, **kwargs)
    
  def __iter__(self):
    batches = list(super().__iter__())
    random.shuffle(batches)
    for batch in batches:
      yield batch
      
class ShufflingBatchSampler(torch.utils.data.sampler.BatchSampler):
  
  def __init__(self, batchsampler, shuffle = True, seed = 10101):
    self.batchsampler = batchsampler
    self.shuffle = True
    self.seed = seed
    self.numitercalls = -1
    
  def __iter__(self):
    self.numitercalls += 1
    batches = self.batchsampler.__iter__()
    if self.shuffle:
      batches = list(batches)
      random.seed(self.seed+self.numitercalls)
      random.shuffle(batches)
    for batch in batches:
      yield batch
      
  def __len__(self):
    return len(self.batchsampler)
      

'''
Test the sampler:
  
  [[chr(i+ord('a')) for i in batch] for batch in EvenlyDistributingSampler(SequentialSampler(list(range(25))), batch_size=4, drop_last=True)]
  
'''      
class EvenlyDistributingSampler(torch.utils.data.sampler.BatchSampler):
  
  def __init__(self, sampler, batch_size, drop_last, *args, **kwargs):
    super(EvenlyDistributingSampler, self).__init__(sampler, batch_size, drop_last, *args, **kwargs)
    if not drop_last:
      raise NotImplementedError('Drop last is not yet implemented for `EvenlyDistributingSampler`.')
    self.sampler = sampler
    self.batch_size = batch_size
    
  def __iter__(self):        
    # Starting from sequential data, batchify arranges the dataset into columns.
    # For instance, with the alphabet as the sequence and batch size 4, we'd get
    # ┌ a g m s ┐
    # │ b h n t │
    # │ c i o u │
    # │ d j p v │
    # │ e k q w │
    # └ f l r x ┘.
    # These columns are treated as independent by the model, which means that the
    # dependence of e. g. 'g' on 'f' can not be learned, but allows more efficient
    # batch processing.
    #  def batchify(data, bsz):
    #    # Work out how cleanly we can divide the dataset into bsz parts.
    #    nbatch = data.size(0) // bsz
    #    # Trim off any extra elements that wouldn't cleanly fit (remainders).
    #    data = data.narrow(0, 0, nbatch * bsz)
    #    # Evenly divide the data across the bsz batches.
    #    data = data.view(bsz, -1).t().contiguous()
    #    return data.to(device)
    
    # get_batch subdivides the source data into chunks of length args.bptt.
    # If source is equal to the example output of the batchify function, with
    # a bptt-limit of 2, we'd get the following two Variables for i = 0:
    # ┌ a g m s ┐ ┌ b h n t ┐
    # └ b h n t ┘ └ c i o u ┘
    # Note that despite the name of the function, the subdivison of data is not
    # done along the batch dimension (i.e. dimension 1), since that was handled
    # by the batchify function. The chunks are along dimension 0, corresponding
    # to the seq_len dimension in the LSTM.
    #  def get_batch(source, i):
    #    seq_len = min(args.bptt, len(source) - 1 - i)
    #    data = source[i:i+seq_len]
    #    target = source[i+1:i+1+seq_len].view(-1)
    #    return data, target
    
    # tests:
    # data = torch.Tensor([i for i in range(ord('a'),ord('z')+1)]).long()
    # [xyz = chr(i) for i in [for r in data]]
    #
    
    # each sampler returns indices, use those indices
    data = torch.LongTensor(list(self.sampler))
    nbatch = data.size(0) // self.batch_size
    data = data.narrow(0, 0, nbatch * self.batch_size)
    data = data.view(self.batch_size, -1).t() # this is important!
    
    for row_as_batch in data:
      yield row_as_batch.tolist()
      
class SimpleRepl(object):
  def __init__(self, evaluator=lambda cmd: print("You entered '%s'." % cmd), PS1 = '>> '):
    self.ps1 = PS1
    self.evaluator = evaluator

  def read(self):
    return input(self.ps1)

  def evaluate(self):
    command = self.read()
    return self.evaluator(command)

  def run(self):
    while True:
      try:
        self.evaluate()
      except KeyboardInterrupt:
        print('Bye Bye\n')
        break