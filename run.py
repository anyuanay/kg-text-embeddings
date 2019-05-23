import pickle 
import os 
import random 
import argparse 
import time 
import multiprocessing
import ctypes
import numpy as np 
import gensim 
import nltk 
import kg_embedding 



stopwords = set(nltk.corpus.stopwords.words('english'))
stopwords.add("</s>")

def preprocess(text):
    return [word for word in gensim.utils.simple_preprocess(text,min_len=1,max_len=50) if word not in stopwords]

my_randint = lambda n : int(n * random.random())

def negative_sample(fact, ne):
    if random.random() < 0.5:
        return (my_randint(ne), fact[1], fact[2])
    else:
        return (fact[0], fact[1], my_randint(ne))


def shared_array(dim):
    shared_array_base = multiprocessing.Array(ctypes.c_double, dim)
    shared_array = np.ctypeslib.as_array(shared_array_base.get_obj())
    return shared_array


class RunKGText(object):
    def __init__(self, dataset, scoref, strategy=None):
        self.scoref = scoref 
        self.strategy = strategy 
        
        self._load_text(dataset)             
        self._load_graph(dataset)
        print("Loaded:")
        print("\t%i entities" %(len(self.entities)))
        print("\t%i relations" %(len(self.relations)))
        print("\t%i facts" %(len(self.facts)))
        
        if self.strategy is not None:
            google_vecs = gensim.models.KeyedVectors.load_word2vec_format('data/GoogleNews-vectors-negative300.bin', binary=True)
            self.word_ids_names, self.word_init_names, self.W_text = self._build_text_data(self.name_vocab, self.entity2name, google_vecs)
            self.word_ids_desc, self.word_init_desc, _ = self._build_text_data(self.desc_vocab, self.entity2desc, google_vecs)           

            if self.strategy == "WV-names":
                print("\t%i words" %(len(self.name_vocab)))
            elif self.strategy == "WV-desc":
                print("\t%i words" %(len(self.desc_vocab)))


    def _load_graph(self, dataset):
        print("Loading knowledge graph") 
        # Load triplets from text files 
        self.facts = [] 
        self.entities = set([])
        self.relations = set([])

        for i,fname in enumerate(["train.txt","valid.txt","test.txt"]):
            if dataset == "WN":
                fname = os.path.join("data","WN18",fname)
            else:
                fname = os.path.join("data","FB15k",fname)
            with open(fname) as f:
                for line in f:
                    subj, pred, obj = line.split()
                    
                    # Only include entities (and facts) for which names and descriptions are available. 
                    # Even though names and descriptions are only used for the WV-names and WV-desc methods, 
                    # we need to do this for all methods to ensure a fair comparison. 
                    if (subj not in self.entity2name or obj not in self.entity2name or subj not in self.entity2desc 
                        or obj not in self.entity2desc): 
                        continue 
                        
                    self.facts.append((subj, pred, obj, 1<<i))
                    self.entities.add(subj)
                    self.entities.add(obj)
                    self.relations.add(pred)

        # Convert strings to indices 
        self.entities = list(self.entities)
        self.relations = list(self.relations)
        entity2id = dict(zip(self.entities, range(len(self.entities))))
        relation2id = dict(zip(self.relations, range(len(self.relations))))
        self.facts = [(entity2id[s], relation2id[p], entity2id[o], d) for s,p,o,d in self.facts]


    def _load_text(self, dataset):
        print("Loading text data") 
        # Load text 
        self.name_vocab = set([])
        self.desc_vocab = set([])
        self.entity2name = {}
        self.entity2desc = {} 

        if dataset == "WN":
            fname = os.path.join("data","WN18","descriptions.txt")
        elif dataset == "FB":
            fname = os.path.join("data","FB15k","descriptions.txt")
        else:
            raise ValueError("Invalid dataset argument")

        with open(fname) as f:
            for line in f:
                try:
                    entity, name, desc = line.split("\t")
                except:
                    print(line)
                    assert False 

                # "Unknown" in Freebase indicates data could not be found 
                if dataset == "WN":
                    name = name[2:].split("_")[:-2]
                    self.name_vocab = self.name_vocab.union(set(name))
                else:
                    if name == "Unknown":
                        name = None 
                    else:
                        name = preprocess(name) 
                        self.name_vocab = self.name_vocab.union(set(name))

                if dataset == "FB" and desc == "Unknown":
                    desc = None 
                else:
                    desc = preprocess(desc)
                    self.desc_vocab = self.desc_vocab.union(set(desc))

                self.entity2desc[entity] = desc 
                self.entity2name[entity] = name             


    def _build_text_data(self, vocab, entity2text, google_vecs): 
        '''
        Build data structures needed for embedding models with text
        '''
        word2id = dict(zip(vocab, range(len(vocab))))
        word_ids = [] 
        for e in self.entities:
            word_ids.append([word2id[word] for word in entity2text[e]])

        word_init = [google_vecs[word] if word in google_vecs else None for word in vocab]
        W_text = [] 
        for e in self.entities:
            vectors = [google_vecs[word] for word in entity2text[e] if word in google_vecs]
            if len(vectors) == 0:
                W_text.append(None)
            else:
                W_text.append(np.mean(vectors, axis=0))

        return word_ids, word_init, W_text 


    def run_embedding(self, dim, **kwargs):
        print("Training")
        X_train = [(s,p,o) for s,p,o,d in self.facts if d & (1<<0)]
        X_valid = [(s,p,o) for s,p,o,d in self.facts if d & (1<<1)]

        if self.strategy == "FeatureSum":
            cls = eval("kg_embedding."+self.scoref+"FeatureSum")
            self.model = cls(len(self.entities), len(self.relations), dim, negative_sample, self.W_text, **kwargs)

        elif self.strategy == "WV-names":
            cls = eval("kg_embedding."+self.scoref+"WordVectors")
            self.model = cls(len(self.entities), len(self.relations), dim, negative_sample, self.word_ids_names, 
                self.word_init_names, **kwargs)

        elif self.strategy == "WV-desc":
            cls = eval("kg_embedding."+self.scoref+"WordVectors")
            self.model = cls(len(self.entities), len(self.relations), dim, negative_sample, self.word_ids_desc, 
                self.word_init_desc, **kwargs)

        else:
            cls = eval("kg_embedding."+self.scoref)
            self.model = cls(len(self.entities), len(self.relations), dim, negative_sample, **kwargs)

        self.model.fit(X_train) 


    def _predict_ranking(self, si, pi, oi):
        negs = list(range(self.model.ne))
        oiz = [oi] + negs 
        all_scores = self.model.score(si, pi, oiz)
        assert(all_scores.shape == (len(negs)+1,))
        rank_obj = np.sum(all_scores > all_scores[0]) + 1

        siz = [si] + negs 
        all_scores = self.model.score(siz, pi, oi)
        assert(all_scores.shape == (len(negs)+1,))
        rank_subj = np.sum(all_scores > all_scores[0]) + 1

        return rank_obj, rank_subj


    def _evaluate_proc(self, X_test, save_idx, shared_results):
        tot = len(X_test) 
        cnt = 0
        results = []

        for s,p,o in X_test:
            start = time.time()
            rank1, rank2 = self._predict_ranking(s, p, o)
            results.append(rank1)
            results.append(rank2)

            t = time.time() - start 
            cnt += 1
            if cnt%10 == 0:
                print("Progress: %i/%i (%.1f)              " %(cnt,tot,t), end='\r')
        
        shared_results[save_idx : save_idx+len(results)] = np.array(results) 


    def evaluate(self, n_jobs=2):
        print("Evaluating")
        X_test = [(s,p,o) for s,p,o,d in self.facts if d & (1<<2)]
        ranks = []
        tot = len(X_test)
        shared_results = shared_array(2*tot)

        procs = []
        facts_per_thread = (tot+n_jobs-1) // n_jobs 
        start_idx = 0
        for i in range(n_jobs):
            p = multiprocessing.Process(target=self._evaluate_proc, args=(X_test[start_idx : start_idx+facts_per_thread], 2*start_idx, shared_results))
            p.start()
            procs.append(p)
            start_idx += facts_per_thread

        for p in procs:
            p.join()
        print('')

        ranks = np.array(shared_results)
        mean_rank = np.mean(ranks)
        hits = np.mean(ranks <= 10)
        print("Mean rank: %.2f" %(mean_rank))
        print("Hits@10: %.3f" %(hits))




def init(parser):
    parser.add_argument("dataset", help="WN or FB")
    parser.add_argument("scoref", help="Name of scoring function for embedding model. Must be one of" \
                                        "'SE', 'TransE', 'TransR', 'RESCAL', 'DistMult', 'HolE'")
    parser.add_argument("--strategy", help="Text enhancement strategy: FeatureSum, WV-names, or WV-desc")
    parser.add_argument("--weighted", action="store_true", default=False, help="Do weighted word vectors (WWV)")
    parser.add_argument("--word_init", action="store_true", default=False, help="Initialize word vectors with word2vec vectors")
    parser.add_argument("--pe", action="store_true", default=False, help="Do paramemter-efficient weighted word vectors (PE-WWV)")
    parser.add_argument("--tfidf", action="store_true", default=False, help="Do word vectors with tf-idf word weights")
    parser.add_argument("--dim", type=int, default=100, help="Embedding dimensionality")
    parser.add_argument("--epochs", type=int, default=200, help="Number of training epochs")
    parser.add_argument("--batch_size", type=int, default=1024, help="Training batch size")
    parser.add_argument("--margin", type=float, default=1.0, help="Ranking loss margin")
    parser.add_argument("--n_jobs", type=int, default=1, help="Number of processes with which to run link prediction")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    init(parser)
    args = parser.parse_args()

    runner = RunKGText(args.dataset, args.scoref, args.strategy)

    runner.run_embedding(dim=args.dim, weighted=args.weighted, word_init=args.word_init, pe=args.pe, tfidf_weights=args.tfidf, 
        epochs=args.epochs, batch_size=args.batch_size, margin=args.margin)

    runner.evaluate(n_jobs=args.n_jobs)
